# anniv — 纪念日

两屏轮播，全部静止不动：一眼读完才是这块 52×16 屏的用法。

| 屏 | 画面 | 内容 |
| --- | --- | --- |
| names | `AMY` ♥ `BEN` 整屏居中 | 两个名字，中间那个 `&` 画成 7×6 爱心 |
| days | 卡比 + 数字 + Yukio | 在一起的天数，例如 `5152` |

两个人各自喜欢的角色一边一个（他喜欢星之卡比，她喜欢 [ゆきお / Yukio](https://am-sanwa.co.jp/character/yukio01.html)
——A&M サンワ 的「天空の非公認キャラクター」），日子夹在中间。
没有倒数屏：`5152` 这个一直在涨的数字本身就是要看的东西，再加一屏「还有几天」只是把同一件事
说两遍，反而要多等一轮才转回来。

### 为什么不写整句

第一版写的是 `TOGETHER 5152 DAYS` 这种整句，52px 放不下，只能学 `plugins/notice` 逐帧左移做跑马灯。
跑马灯要**盯着看完一遍**才知道说什么，一句 18 个字要滚 12 秒，桌面余光扫一眼什么都读不到，
而且 4fps 推流一停（笔记本睡了）就定格在半句上。改成静止短词后，一眼就是全部信息，
定格也仍然是一个完整画面。这是这个插件最主要的取舍。

### 排版是算出来的，不是试出来的

设备字体固定 `fontHeight 10`、一个字 6px、不缩放、只认大写 ASCII（`core.ascii_upper`）。

- **数字屏**：`12(卡比) + 2 + 24(数字) + 2 + 12(Yukio) = 52`，左右完全对称。公仔画成 12px 而不是 14px
  就是为了这两道 2px 缝——14px 时数字会贴到卡比脸上（第一版就是这样，一眼看出来是歪的）。
  中间 24px = 4 个字；天数涨到 5 位数（2039-12-28）时自动退回 wide 版式：只留卡比，右边 36px 放字。
- **公仔自己也要左右对称**。卡比第一版的脚是 `.RRRR..RRRR...`，右边多两列空白，摆在屏幕左边
  看起来整只往左偏。有测试逐行比左右留白，改字符画时会挡住这类手滑。
- **名字屏**：不放卡比，整屏 52px 都给名字。`(len(a)+len(b))*6 + 7(爱心) + 2(缝) ≤ 52`
  → 两个名字加起来 ≤ 7 个字母才写全名（`AMY`+`BEN` = 6，`ERIC`+`CYNTHIA` = 11 放不下），
  超了就退回首字母 `E ♥ C`。规则写在 `fit_names()`，别改成截断——截断出来的 `ERI ♥ CYN` 谁都不认得。

### 像素画

`sprite()` 把字符画转成一条 `db` 指令（`[x, y, w, h, 行优先像素]`）。`db` 连背景一起画，
屏幕本来就是黑的，所以 `.` 直接给 0 当透明用，不需要 base64 图片、也不需要 Pillow。

- 卡比 12×12：粉身 `#FFAEC9`、深蓝眼 `#24325E` 带白高光、腮红 `#FF5C8A`、红脚 `#E23A3A`。
- Yukio 12×12（另一套调色板 `YUKIO_PALETTE`）：通体纯白，脸上只有两道眼缝、一点鼻子、一张小嘴，
  那三处黑其实就是不点亮。这种「白底大头 + 三处黑」的造型最吃得住低分辨率，12px 也还认得出是谁。
  两只都是照印象手画的致敬，不是官方素材；私人桌面玩具用，别拿去卖。
- 爱心 7×6，只用在名字屏中间。三张画都在 `plugin.py` 顶部，改字符画即可改形状，
  行宽必须一致、左右必须对称（都有测试盯着）。

一帧里 `draw` 和 `text` 同时给，画在左、字在右，x 区间不重叠，所以不依赖设备的图层顺序。

### 名字和日期记在 .pixbar.json

`anniv_names` / `anniv_date` 两个键。这两个值长期不变，不该每次重填；也**不写死在仓库里**——
`origin` 是公开 repo，纪念日和名字是私事。所以：

- 面板文本框改一次、或命令行 `--set` 给一次，就写回配置，下次自动带出来（`OPTIONS` 的 default 在 import 时读）。
- 写盘必须**读-改-写**：同一份文件还存着设备地址与传输设置，整份覆盖会丢掉它们。
  文件存在但解析失败时**宁可不写**（这点和面板的 `_load_config()` 不同，那条路返回 `{}` 会静默丢键）。
- 面板里一个字一个字打 `2019-02-1` 的中间态不写盘，免得盖掉已存的好日期。

### 跑法

```bash
python3 plugins/anniv/plugin.py --device <IP> --set names="Kit & Sze" --set date=YYYY-MM-DD
python3 plugins/anniv/plugin.py --device <IP>          # 之后这样就够, 名字日期已记住
python3 -m unittest tests.test_anniv
```

### 挂在一台常开的机器上

这块屏的画面是一帧一帧推出来的, 没有进程推就不会变。macOS 上用 launchd 开机自启,
本目录有现成模板 `com.pixdeck.anniv.plist`:

```bash
sed "s|__PIXDECK__|$PWD|g; s|__DEVICE__|<时钟IP>|g" plugins/anniv/com.pixdeck.anniv.plist \
  > ~/Library/LaunchAgents/com.pixdeck.anniv.plist

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.pixdeck.anniv.plist   # 装
launchctl kickstart -k gui/$(id -u)/com.pixdeck.anniv                             # 改完代码重启
launchctl bootout gui/$(id -u)/com.pixdeck.anniv                                  # 卸
tail -f /tmp/pixdeck-anniv.log                                                    # 看日志
```

跑的是插件自己而不是面板 —— **两者别指同一台钟**: 面板开机会扫掉设备上所有本工具的组件,
运行中的 `reconcile()` 也会删掉"插件没在面板里开着"的组件, 而 standalone 推的这个对面板来说
正是"没开着"。

`KeepAlive` 让它挂了自动重起, `ThrottleInterval 30` 防止写错参数时疯狂重启。
LaunchAgent 只在登录态下跑, 机器睡了它也睡 —— 要真的常年准, 这台机器就别让它睡。

### 已知取舍

- **要有进程在推**。停掉进程画面会**定格在最后一屏**（stock firmware 忽略 custom app 的 `lifetime`，
  组件会一直显示最后一帧，直到有人推 `{}` 删掉它）。所以笔记本一睡，天数就停在那天不再更新——
  数字仍然是对的，只是不会跳。想真正常年准，得把这条命令挂在一台常开的机器上（纯标准库，树莓派/NAS 都跑得动）。
- **一台面板只认一个设备**。家里和公司各一台 T00002 时，面板的 `device` 只存一个，
  公司那台的 MAC 末四位写在 `prefix`（`ulanzi_xxxx`）里、ARP 靠它找 IP。
  两台并存要改成按 MAC 存多台，`Runner`/`reconcile`/`watchdog` 都假设只有一个设备。
