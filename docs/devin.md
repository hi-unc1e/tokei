# Devin

| 卡片 | 额度来源 | Token 来源 | 默认 |
|---|---|---|---|
| Devin | 桌面端保存的套餐（本地 SQLite） | Devin CLI 会话库（本地 SQLite） | 开 |

两个来源互不相干：一个是账号额度，一个是 CLI 自己的对话记录，彼此不知道对方的存在。
任何一半有数据就画那一半。全程不联网、不需要凭据、不碰 Keychain，也不需要完全磁盘访问权限。

开关：设置里的 **Devin** 一项，同时控制卡片可见性与额度读取（`devin_quota_enabled`）。
环境变量 `TOKEI_DEVIN_QUOTA=0` 强制关闭、`=1` 强制开启。

## 额度：桌面端保存的套餐

Devin 的 Mac 端是**改名后的 Windsurf 编辑器**——`/Applications/Devin.app` 的 bundle id
仍然是 `com.exafunction.windsurf`，它写出的每一个键也仍然叫 `windsurf.*`。这层继承就是
整条路线：它是一个 VS Code fork，所以 globalStorage 就是一个普通 SQLite 文件，账号最近
一次读到的套餐是其中一行。

```sql
SELECT key, value FROM ItemTable
 WHERE key LIKE 'windsurf.reactSettings.cachedPlanInfoData%'
```

键上带着账号 id：`windsurf.reactSettings.cachedPlanInfoData:user-<32 hex>`。
旧键名 `windsurf.settings.cachedPlanInfo` 仅作兜底再查一次。

**支持目录按两个名字找，取最新的一个**：Electron 应用的支持目录跟随产品名，所以改名前
装过 Windsurf 的机器留下 `Application Support/Windsurf`，新装的是 `Application Support/Devin`。

### 画出来的东西

| 字段 | 落到哪 | 说明 |
|---|---|---|
| `dailyRemainingPercent` | 日额度，1440 分钟 | `hideDailyQuota` 为真时整个丢掉 |
| `weeklyRemainingPercent` | 周额度，10080 分钟 | `hideWeeklyQuota` 为真时整个丢掉 |
| `remainingMessages` / `totalMessages` | 消息额度 | 免费套餐的消息池，没有周期也没有重置 |
| `overageBalanceMicros` | 「超额余额」明细 | micros，10,000,000 即十美元 |
| `planName` | 套餐名（`Devin Pro`） | |
| `accountIdentityText` | 账号行 | 形如 `someone@example.com - My Team` |

**没有一处是推算出来的。** Devin 自己同时给出两个百分比和两个重置时刻，所以
`已用 = 100 − 剩余`，窗口长度也用它真的给了的那个。

付费套餐把消息计数写成 `-1`，那是「不适用」的写法而不是计数——按计数读会画出
「负一分之负一」，所以任一端为负都按没有这项处理。布尔值同理不当数字：
`hideDailyQuota` 若走进取数分支会变成 1%。

### 局限：这是一张「启动时」的快照

**那一行是应用启动时写入的，不是运行中持续刷新的。** 所以读数的落款是那次启动，不是现在。

时间取自 `logs/`，每次运行建一个目录，**目录名**就是启动时刻（`20260920T214706`，本机时区）。
用目录名而不是目录的修改时间：修改时间会往后跑——在已有文件里追加不动它，但会话进行
一小时后新建的日志文件会把它顶上去，顶上去多少分钟，卡片就把这份自启动起就没变过的
读数少算多少分钟。

由此：

- 10 分钟内算当前读数；超过就标记为过期，卡片上写「额度更新于 …」。
- **重置时刻已过的窗口直接丢掉，而不是继续挂着**：那个数字属于一个已经不存在的窗口。
  另一个窗口和余额（本来就没有重置）照常保留。
- 超过 24 小时没重启过，这行数据不再展示。解法就是打开一次 Devin，那会写入新的一行。
- **落款不可信的快照同样不展示**，而不是拿抓取时刻替它落款：数据库自身的修改时间被
  文件里其它每一个键刷新着，早餐时读到的数会显示成一秒前的。没落款不等于新鲜。

### 一台机器上的两个账号

登录过两个账号就会有两行，而文件里没有任何字段说明哪个是当前的。取 `endTimestamp`
最远的一行——还在订阅期内的套餐优先于已过期的——并在明细里写明「本机账号 N 个
（取订阅期最长的一个）」，不把这个选择藏起来。

## Token：Devin CLI 的会话库

`~/.local/share/devin/cli/sessions.db`。`message_nodes.chat_message` 是整条消息的 JSON，
assistant 那条在 `metadata.metrics` 里带着自己的用量：

```json
{ "role": "assistant", "metadata": {
    "generation_model": "swe-1-6-slow",
    "created_at": "2026-09-20T13:49:51.721142Z",
    "metrics": { "input_tokens": 21, "output_tokens": 92,
                 "cache_read_tokens": 29504, "cache_creation_tokens": null } } }
```

`input_tokens` 与两个缓存桶**并列**（Anthropic 的口径），不做相减——实测中见过
`input_tokens` 21 而 `cache_read_tokens` 29504，相减会把它压成 0。

时刻优先取消息自己的 `created_at`（ISO 字符串，微秒精度，是这次回复真正的时间）；
行上的 `created_at` 只精确到秒，作兜底。缺时间戳的消息跳过，不拿文件修改时间顶替。
模型名缺失时用 `sessions.model` 兜底。

### 兄弟节点会把用量翻一倍

`message_nodes` 是一片**森林**（`node_id` / `parent_node_id`）。实测中，同一次回复会被
写进共用一个 parent 的两个兄弟节点，`metrics` 与 `metadata.created_at` 完全一致：

```
session       node_id  parent  meta_created_at                input_tokens
near-ravioli  26       25      2026-09-20T13:49:51.721142Z    17349
near-ravioli  27       25      2026-09-20T13:49:51.721142Z    17349
```

照单全收会把用量整整翻一倍（实测 34,740 对 17,370）。所以同一会话里
「同一微秒时刻 + 同一组 metrics + 同一模型」只计一次。两次不同的调用不会既同时刻
又同用量；而没有微秒落款可比时退回按节点计，宁可不合并也不丢数。

### 计量口径版本

`_DEVIN_COST_VERSION` 同时用作扫描缓存的版本与账本天的 `_cost_version`。改动解析口径时
把它 +1：账本按 `_cost_version` 取新不取大，否则被高水位规则记下的旧数（例如上面那版
翻倍的）会一直压住修正后的值。

## 没有实现的：实时接口

Devin 另有一个 `GET https://app.devin.ai/api/<org>/billing/quota/usage` 的实时额度接口，
它的 Bearer token 要从 Chromium 浏览器的 localStorage（LevelDB）里读。Tokei 目前没有
任何浏览器存储读取器，这条路线未实现。上面两条本地路线都不需要它。

注意该接口报的是**已用**（`daily_percentage`），而本地那行报的是**剩余**
（`dailyRemainingPercent`）——两者反向，若将来实现不要反转两次。
