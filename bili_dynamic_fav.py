#!/usr/bin/env python3
"""Collect recent followed Bilibili video dynamics and add videos to a favorite list.

Flow:
    Bilibili dynamic video tab -> followed dynamic feed -> last N days -> video posts
    -> video duration >= minimum seconds -> add to target favorite folder.

Authentication:
    Export a browser cookie string after logging in to Bilibili and pass it with
    --cookie, --cookie-file, or the BILI_COOKIE environment variable. The cookie
    must include bili_jct for write operations.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from urllib.error import HTTPError
from urllib.parse import urlencode, unquote
from urllib.request import Request, build_opener

import config

# 关注动态流接口：对应 B 站动态页的视频投稿 Tab。
DYNAMIC_FEED_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all"
# 视频详情接口：用于根据 bvid/aid 查询 aid 与视频时长。
VIDEO_VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
# 收藏操作接口：用于把视频添加到指定收藏夹。
FAVORITE_DEAL_URL = "https://api.bilibili.com/x/v3/fav/resource/deal"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class DynamicVideo:
    """从动态流中提取出的单个视频投稿信息。"""
    dynamic_id: str
    bvid: str
    aid: int | None
    title: str
    pub_ts: int
    up_name: str


class BilibiliDynamicFavoriter:
    """负责扫描动态流、筛选视频并执行收藏的主流程类。"""
    def __init__(
        self,
        cookie: str,
        media_id: int,
        state_path: Path,
        days: int,
        min_duration: int,
        page_sleep: float,
        action_sleep: float,
        dry_run: bool,
    ) -> None:
        self.media_id = media_id
        self.state_path = state_path
        self.days = days
        self.min_duration = min_duration
        self.page_sleep = page_sleep
        self.action_sleep = action_sleep
        self.dry_run = dry_run
        self.cookie = cookie
        self.opener = build_opener()
        self.headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": "https://t.bilibili.com/?tab=video",
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Cookie": cookie,
        }
        # B 站收藏接口需要 bili_jct 作为 CSRF token。
        self.csrf = read_cookie_value(cookie, "bili_jct")
        self.state = load_state(state_path)

    def run(self) -> dict[str, int]:
        stats = {
            "seen_dynamic": 0,
            "candidate_video": 0,
            "skipped_processed": 0,
            "skipped_old": 0,
            "skipped_short": 0,
            "favorited": 0,
            "failed": 0,
        }
        # 以当前时间倒推 days 天作为动态发布时间筛选边界。
        cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=self.days)).timestamp())
        logging.info("Start scanning followed video dynamics since %s", format_ts(cutoff_ts))

        for video in self.iter_recent_video_dynamics(cutoff_ts):
            stats["candidate_video"] += 1
            # 使用 bvid 优先作为去重键；没有 bvid 时退回 aid。
            dedupe_key = video.bvid or str(video.aid)
            if dedupe_key in self.state["processed_bvids"]:
                stats["skipped_processed"] += 1
                logging.info("Skip processed: %s %s", video.bvid, video.title)
                continue

            try:
                # 动态卡片里的信息不一定完整，先查询视频详情确认 aid 与时长。
                aid, duration = self.fetch_video_info(video)
                if duration < self.min_duration:
                    stats["skipped_short"] += 1
                    logging.info(
                        "Skip short video: %s duration=%ss title=%s",
                        video.bvid,
                        duration,
                        video.title,
                    )
                    self.mark_processed(dedupe_key, "short")
                    continue

                if self.dry_run:
                    logging.info(
                        "Dry-run favorite: aid=%s bvid=%s duration=%ss title=%s",
                        aid,
                        video.bvid,
                        duration,
                        video.title,
                    )
                else:
                    # 只有通过时间窗口、视频类型和时长筛选后才真正收藏。
                    self.add_to_favorite(aid)
                    logging.info(
                        "Favorited: aid=%s bvid=%s duration=%ss up=%s title=%s",
                        aid,
                        video.bvid,
                        duration,
                        video.up_name,
                        video.title,
                    )
                stats["favorited"] += 1
                self.mark_processed(dedupe_key, "favorited" if not self.dry_run else "dry_run")
                time.sleep(self.action_sleep)
            except HTTPError as exc:
                stats["failed"] += 1
                logging.exception("HTTP failure for %s: %s", video.bvid, exc)
            except (KeyError, TypeError, ValueError) as exc:
                stats["failed"] += 1
                logging.exception("Parse failure for %s: %s", video.bvid, exc)

        self.save_state()
        logging.info("Finished with stats: %s", stats)
        return stats

    def iter_recent_video_dynamics(self, cutoff_ts: int) -> Any:
        # B 站动态流使用 offset 分页，第一页传空字符串。
        offset = ""
        page = 0
        while True:
            page += 1
            payload = self.get_json(
                DYNAMIC_FEED_URL,
                params={"type": "video", "offset": offset, "timezone_offset": -480},
            )
            data = payload.get("data") or {}
            items = data.get("items") or []
            if not items:
                logging.info("No more dynamic items at page %s", page)
                return

            oldest_ts_on_page = int(time.time())
            for item in items:
                dynamic_id = str(item.get("id_str") or item.get("id") or "")
                modules = item.get("modules") or {}
                pub_ts = extract_pub_ts(modules)
                oldest_ts_on_page = min(oldest_ts_on_page, pub_ts)
                # 动态发布时间早于 cutoff 时跳过；整页越界后会停止翻页。
                if pub_ts < cutoff_ts:
                    logging.debug("Reached old dynamic %s at %s", dynamic_id, format_ts(pub_ts))
                    continue
                video = extract_video(item)
                if video is None:
                    continue
                yield video

            has_more = bool(data.get("has_more"))
            offset = str(data.get("offset") or "")
            if not has_more or not offset:
                return
            # 当前页最早动态已经超过时间窗口，后续页通常更旧，可以停止。
            if oldest_ts_on_page < cutoff_ts:
                logging.info("Stop after page %s; oldest item is older than cutoff", page)
                return
            time.sleep(self.page_sleep)

    def fetch_video_info(self, video: DynamicVideo) -> tuple[int, int]:
        params: dict[str, str | int] = {"bvid": video.bvid} if video.bvid else {"aid": video.aid or 0}
        payload = self.get_json(VIDEO_VIEW_URL, params=params)
        data = payload["data"]
        return int(data["aid"]), int(data["duration"])

    def add_to_favorite(self, aid: int) -> None:
        # 没有 bili_jct 时无法通过 B 站收藏接口的 CSRF 校验。
        if not self.csrf:
            raise ValueError("cookie is missing bili_jct; cannot submit favorite request")
        payload = self.post_json(
            FAVORITE_DEAL_URL,
            data={
                "rid": aid,
                "type": 2,
                "add_media_ids": self.media_id,
                "del_media_ids": "",
                "csrf": self.csrf,
            },
        )
        # code=0 表示成功；11201 通常表示已经收藏过，按幂等成功处理。
        code = int(payload.get("code", -1))
        if code not in (0, 11201):
            raise ValueError(f"favorite API failed: {payload}")

    def get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        query = urlencode(params)
        request = Request(f"{url}?{query}", headers=self.headers, method="GET")
        with self.opener.open(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if int(payload.get("code", 0)) != 0:
            raise ValueError(f"GET {url} failed: {payload}")
        return payload

    def post_json(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        body = urlencode(data).encode("utf-8")
        headers = {**self.headers, "Content-Type": "application/x-www-form-urlencoded"}
        request = Request(url, data=body, headers=headers, method="POST")
        with self.opener.open(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def mark_processed(self, bvid: str, status: str) -> None:
        self.state["processed_bvids"][bvid] = {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.save_state()

    def save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再替换，降低中途退出导致状态文件损坏的概率。
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.state_path)


def extract_pub_ts(modules: dict[str, Any]) -> int:
    """从动态模块中提取发布时间戳。"""
    module_author = modules.get("module_author") or {}
    return int(module_author.get("pub_ts") or 0)


def extract_video(item: dict[str, Any]) -> DynamicVideo | None:
    """只从动态条目中提取 archive 视频投稿，非视频动态返回 None。"""
    modules = item.get("modules") or {}
    major = (modules.get("module_dynamic") or {}).get("major") or {}
    archive = major.get("archive") or {}
    if not archive:
        return None

    bvid = str(archive.get("bvid") or "")
    aid = archive.get("aid")
    if not bvid and not aid:
        return None

    author = modules.get("module_author") or {}
    title = str(archive.get("title") or "").strip()
    return DynamicVideo(
        dynamic_id=str(item.get("id_str") or item.get("id") or ""),
        bvid=bvid,
        aid=int(aid) if str(aid or "").isdigit() else None,
        title=title,
        pub_ts=extract_pub_ts(modules),
        up_name=str(author.get("name") or ""),
    )


def load_state(path: Path) -> dict[str, Any]:
    """读取断点状态文件；不存在时返回空状态。"""
    if not path.exists():
        return {"processed_bvids": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    state.setdefault("processed_bvids", {})
    return state


def read_cookie_value(cookie_string: str, name: str) -> str:
    match = re.search(rf"(?:^|;\s*){re.escape(name)}=([^;]+)", cookie_string)
    return unquote(match.group(1)) if match else ""


def read_cookie(args: argparse.Namespace) -> str:
    """按 命令行 > config.py > 环境变量 的顺序读取 Cookie。"""
    if args.cookie:
        return args.cookie.strip()
    cookie_file = args.cookie_file or config.COOKIE_FILE
    if cookie_file:
        return Path(cookie_file).read_text(encoding="utf-8").strip()
    if config.COOKIE:
        return str(config.COOKIE).strip()
    cookie = os.environ.get("BILI_COOKIE", "").strip()
    if cookie:
        return cookie
    raise SystemExit("Missing cookie. Set COOKIE in config.py, or use --cookie, --cookie-file, or BILI_COOKIE.")


def format_ts(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().isoformat()


def configure_logging(log_file: Path, verbose: bool) -> None:
    """同时配置终端日志和文件日志。"""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(log_file, encoding="utf-8")],
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Favorite recent followed-UP video dynamic posts into a Bilibili favorite folder."
    )
    parser.add_argument("--media-id", type=int, default=config.MEDIA_ID, help="Target favorite folder media_id.")
    parser.add_argument("--cookie", help="Raw Bilibili cookie string copied from a logged-in browser.")
    parser.add_argument("--cookie-file", help="Path to a text file containing the raw cookie string.")
    parser.add_argument("--days", type=int, default=config.DAYS, help="Only process dynamics in the latest N days.")
    parser.add_argument("--min-duration", type=int, default=config.MIN_DURATION, help="Minimum video duration in seconds.")
    parser.add_argument("--state", type=Path, default=Path("data/state.json"), help="Checkpoint JSON path.")
    parser.add_argument("--log-file", type=Path, default=Path("logs/bili_dynamic_fav.log"), help="Log file path.")
    parser.add_argument("--page-sleep", type=float, default=1.0, help="Delay between dynamic pages.")
    parser.add_argument("--action-sleep", type=float, default=0.8, help="Delay between favorite actions.")
    parser.add_argument("--dry-run", action="store_true", help="Scan and log actions without changing favorites.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    configure_logging(args.log_file, args.verbose)
    if args.media_id is None:
        raise SystemExit("Missing media_id. Set MEDIA_ID in config.py or pass --media-id.")
    cookie = read_cookie(args)
    favoriter = BilibiliDynamicFavoriter(
        cookie=cookie,
        media_id=args.media_id,
        state_path=args.state,
        days=args.days,
        min_duration=args.min_duration,
        page_sleep=args.page_sleep,
        action_sleep=args.action_sleep,
        dry_run=args.dry_run,
    )
    stats = favoriter.run()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
