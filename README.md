# B 站动态视频收藏脚本

`bili_dynamic_fav.py` 用于从 B 站“动态 → 视频投稿”页面对应的关注动态流中抓取已关注 UP 主的视频投稿：默认只处理最近 30 天内发布的动态，只保留视频时长不少于 60 秒的投稿；也可以限制只处理指定关注分组（例如“科技”“影视”）中的 UP 主视频，并自动按发布时间从早到晚收藏到指定收藏夹。

脚本支持断点续跑、去重、运行日志和 dry-run 预演模式，适合手动执行或配合定时任务长期运行。

## 功能流程

```text
B站 → 动态 → 视频投稿（https://t.bilibili.com/?tab=video）
↓
获取关注动态流
↓
按动态时间筛选最近 30 天
↓
只保留视频投稿
↓
可选：只保留指定关注分组中的 UP 主
↓
按发布时间从早到晚排序
↓
查询视频时长 ≥ 60 秒
↓
收藏到指定收藏夹
↓
写入断点状态 / 去重 / 日志
```

## 环境要求

- Python 3.10 或更新版本。
- 已登录 B 站账号的浏览器 Cookie。
- Cookie 中必须包含 `bili_jct`，否则无法提交收藏请求。
- 可以把 Cookie、目标收藏夹、日期范围和视频时长阈值写入 `config.py`。
- 脚本只使用 Python 标准库，不需要安装第三方依赖。

如果仍希望执行依赖安装命令，可以运行：

```bash
python -m pip install -r requirements.txt
```

## 获取运行参数

### 1. 获取 Cookie

1. 在浏览器中登录 B 站。
2. 打开 `https://t.bilibili.com/?tab=video`。
3. 从浏览器开发者工具的网络请求中复制 `bilibili.com` 请求的完整 Cookie。
4. 确认 Cookie 内包含 `SESSDATA`、`DedeUserID` 和 `bili_jct`。

> 注意：Cookie 等同于登录凭证，请不要提交到 Git、截图或发送给他人。

### 2. 获取收藏夹 `media_id`

目标收藏夹需要使用 B 站收藏夹的 `media_id`。可以从收藏夹页面 URL、收藏夹相关网络请求，或浏览器开发者工具中查找。

## 配置文件

可以把常用参数写到 `config.py`，避免每次运行都输入：

```python
COOKIE = "SESSDATA=...; bili_jct=...; DedeUserID=..."
COOKIE_FILE = ""
MEDIA_ID = 123456
DAYS = 30
MIN_DURATION = 60
FOLLOW_GROUPS = ["科技", "影视"]
```

这些配置分别对应 Cookie、Cookie 文件路径、目标收藏夹 `media_id`、视频筛选日期范围、视频时长筛选阈值和允许的关注分组。`FOLLOW_GROUPS` 留空时表示不限制关注分组；填写后脚本只会收藏这些分组内 UP 主的视频。命令行参数优先级更高；也就是说，运行时传入 `--media-id`、`--days`、`--min-duration` 或 `--follow-groups` 会覆盖 `config.py` 中的值。

> 注意：真实 Cookie 是登录凭证。如果仓库会推送到远端或共享给他人，请不要把真实 Cookie 写入已提交的文件中，建议改用 `COOKIE_FILE` 或 `BILI_COOKIE` 环境变量。

## 使用方法

如果已经在 `config.py` 中填写 `COOKIE` 和 `MEDIA_ID`，可以直接运行：

```bash
python bili_dynamic_fav.py
```

也可以通过环境变量传入 Cookie，并用命令行参数覆盖收藏夹、日期和时长配置：

```bash
export BILI_COOKIE='SESSDATA=...; bili_jct=...; DedeUserID=...'
python bili_dynamic_fav.py --media-id 123456 --days 30 --min-duration 60 --follow-groups 科技,影视
```

也可以把 Cookie 放到本地文本文件中，再通过参数读取：

```bash
python bili_dynamic_fav.py \
  --cookie-file ./cookie.txt \
  --media-id 123456 \
  --days 30 \
  --min-duration 60 \
  --follow-groups 科技,影视
```

首次运行前建议先使用 dry-run 模式确认筛选结果，不会真的收藏视频：

```bash
python bili_dynamic_fav.py --media-id 123456 --dry-run
```

## 常用参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--media-id` | `config.py` 中的 `MEDIA_ID` | 目标收藏夹 ID。 |
| `--cookie` | 无 | 直接传入完整 Cookie 字符串。 |
| `--cookie-file` | 无 | 从文件读取完整 Cookie 字符串。 |
| `--days` | `config.py` 中的 `DAYS`，初始为 `30` | 只处理最近 N 天内的动态。 |
| `--min-duration` | `config.py` 中的 `MIN_DURATION`，初始为 `60` | 只收藏时长不少于 N 秒的视频。 |
| `--follow-groups` | `config.py` 中的 `FOLLOW_GROUPS`，初始为空 | 逗号分隔的关注分组名称；留空表示处理全部已关注 UP。 |
| `--state` | `data/state.json` | 断点续跑和去重状态文件。 |
| `--log-file` | `logs/bili_dynamic_fav.log` | 运行日志文件。 |
| `--page-sleep` | `2.0` | 动态分页请求之间的等待秒数。 |
| `--action-sleep` | `3.0` | 每次收藏操作之间的等待秒数。 |
| `--max-retries` | `3` | 对 412、429、5xx 等临时 HTTP 失败的重试次数。 |
| `--retry-sleep` | `10.0` | 临时失败重试前的基础等待秒数；多次失败会指数退避。 |
| `--dry-run` | 关闭 | 只扫描和记录日志，不执行收藏。 |
| `--verbose` | 关闭 | 输出更详细的调试日志。 |

## HTTP 412 / 风控处理

`HTTP Error 412: Precondition Failed` 通常不是脚本解析错误，而是 B 站侧的风控或反爬响应：例如短时间连续收藏过多、Cookie 登录态不完整/过期、请求头与浏览器环境差异较大，或当前 IP/账号触发了临时校验。

脚本已针对这类临时失败做了以下处理：

- 为请求补充更接近浏览器 AJAX 请求的常见请求头。
- 默认把分页间隔调高到 2 秒、收藏间隔调高到 3 秒。
- 对 `412`、`429` 和常见 `5xx` 错误自动重试，默认最多重试 3 次，并使用指数退避等待。
- 日志会尽量打印 HTTP 错误响应体，便于判断是风控、Cookie 失效还是接口参数问题。

如果仍然频繁出现 412，建议：

1. 停止脚本一段时间后再运行，避免继续触发风控。
2. 从已登录浏览器重新复制完整 Cookie，确认包含 `SESSDATA`、`DedeUserID`、`bili_jct`，最好也包含 `buvid3`、`b_nut` 等浏览器生成的标识 Cookie。
3. 进一步放慢请求，例如：`python bili_dynamic_fav.py --action-sleep 8 --page-sleep 5 --retry-sleep 30`。
4. 避免多实例同时运行同一个账号，或在短时间内批量收藏大量视频。

## 断点续跑与去重

脚本会把已处理的视频写入 `--state` 指定的 JSON 文件中，默认路径是 `data/state.json`。每处理完一个视频都会立即写入状态，因此中途退出、网络失败或下次重新运行时，脚本会跳过已经处理过的视频。脚本会先收集时间窗口内的视频并按发布时间从早到晚排序，所以实际收藏顺序会从较早发布的视频开始。

状态文件中会记录视频处理结果，例如：

- `favorited`：已收藏。
- `dry_run`：预演模式下命中。
- `short`：视频时长低于阈值，已跳过。

如需完全重新扫描，可以备份并删除状态文件后再运行。

## 日志

默认日志文件为 `logs/bili_dynamic_fav.log`，同时也会输出到终端。日志会记录扫描开始时间、跳过原因、收藏结果和失败异常，便于排查 Cookie 失效、接口变更、网络异常或收藏夹参数错误等问题。

## 注意事项

- B 站 Web API 可能变化；如果接口返回结构调整，脚本可能需要同步更新解析逻辑。
- 请合理设置 `--page-sleep` 和 `--action-sleep`，避免过于频繁请求。
- 收藏接口需要登录态和 CSRF 参数，Cookie 失效后需要重新复制。
- 请遵守 B 站相关服务条款，仅在个人合理范围内使用。
