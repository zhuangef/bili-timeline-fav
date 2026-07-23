"""bili_dynamic_fav.py 的本地配置。

如果不想每次通过命令行传参，可以在这里填写常用参数。
命令行参数的优先级仍然高于本文件中的配置。

不要提交真实 Cookie。B 站 Cookie 等同于登录凭证。
"""

# B 站完整 Cookie 字符串，例如：
# COOKIE = "SESSDATA=...; bili_jct=...; DedeUserID=..."
COOKIE = ""

# 可选：保存完整 Cookie 字符串的文本文件路径。
COOKIE_FILE = ""

# 目标收藏夹 media_id。保持 None 时，需要运行时传入 --media-id。
MEDIA_ID = None

# 只处理最近 N 天内发布的动态。
DAYS = 30

# 只收藏时长不少于该秒数的视频。
MIN_DURATION = 60

# 只收藏这些关注分组中的 UP 主视频；留空表示不限制关注分组。
# 例如：FOLLOW_GROUPS = ["科技", "影视"]
FOLLOW_GROUPS = []
