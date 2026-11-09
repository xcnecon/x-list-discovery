# X List Discovery

从一份 X/Twitter List 或 CSV 名单发现名单之外的新账户，一次输出两份榜单：

- **共同关注榜**：被多个名单成员关注，但不在原名单中；按不同来源人数排序，不看粉丝量。
- **低粉丝 gem 榜**：同样排除原名单，在共同关注多的基础上，加入可配置的总粉丝上限与平滑分数。

两榜始终排除原名单全部成员。即使某成员没有参与投票，也不会作为“新账户”推荐。

## 安装与离线体验

需要 Python 3.9+：

```bash
python -m pip install git+https://github.com/xcnecon/x-list-discovery.git
```

下载或克隆仓库后，可以不配置 API，直接跑虚构示例：

```bash
x-list-discovery init demo --members examples/members.csv
x-list-discovery import-graph demo examples/followings.json
x-list-discovery run demo
```

结果在 `runs/demo/output/`：共同关注和 gem 各一份 Excel、CSV，以及机器可读的 discovery.json。Excel 包含前 100 名预览、完整候选、共同关注者明细、来源覆盖和统计口径；CSV 包含全部筛选结果。

## 使用自己的名单

在线数据来自第三方 [twitterapi.io](https://twitterapi.io/?ref=thmbmzza)，需自备 API key，抓取消耗该服务额度。复制 `.env.example` 为 `.env`，填写 `TWITTERAPI_IO_KEY`，或在环境变量中设置。

```bash
# 输入 X List 链接或数字 ID
x-list-discovery init research --list https://x.com/i/lists/123456789

# 也可输入本地 CSV/JSON；两种 init 选一种
x-list-discovery init research --members /path/to/members.csv

x-list-discovery fetch research
x-list-discovery run research --min-common 5 --max-followers 50000
```

同名工作目录拒绝覆盖。`--min-common` 是最低不同来源人数；`--max-followers` 仅影响 gem 榜，粉丝数严格小于该上限。默认 `run` 同时生成两榜且不联网；只有显式 `--fetch` 才抓取。

CSV 最少只需 `username` 一列，也支持 userName、screen_name。可附 id/user_id、简介及粉丝/关注数量。默认让名单全部成员投票；本人真实关注网络不会替换这份名单。

## 个人筛选与排重

```bash
# 目标只保留个人，排除 BD/营销角色信号
x-list-discovery init people --members members.csv --people-only --exclude-bd

# 仅用人工选出的部分成员投票；原名单全部成员依然排重
x-list-discovery init curated --members members.csv --seeds personal-seeds.csv

# 可选：自动仅让资料明确为个人的成员投票
x-list-discovery init personal --members members.csv --personal-seeds --people-only

# 本人实际已关注的人仅作为额外目标排除集合
x-list-discovery exclude-followings research --account your_handle
x-list-discovery run research
```

资料分类只作初筛。待确认账号在完整候选表中保留，不把项目创始人的个人号自动当成项目官方号。支持 `--reviews` 人工分类，格式见 examples/profile-reviews.json；名单建好后可编辑 input/reviews.json 并重新运行。

## 缓存与口径

每份名单独立保存输入、缓存、配置和导出。抓取成功的来源自动跳过，失败可重试，显式 `fetch --refresh` 刷新并保留历史。失败或中断不能聚合为空成功；私密/不可获取的来源贡献未知，会在覆盖表说明。

共同关注按不同来源计票，一人最多一票。改名优先用稳定 ID 排重。Gem 分数为 `100 × 共同关注人数 / sqrt(总粉丝数 + 1000)`，平滑项可在 config.json 调整。网络重合与低粉丝量是发现信号，不代表内容质量认证。

更多参数见 [英文 README](README.md)，算法与数据格式见 [methodology](docs/methodology.md)。
