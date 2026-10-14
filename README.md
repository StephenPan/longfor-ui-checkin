# 龙湖会员本地签到 · Longfor UI Check-in

两套在本机操作正常界面的签到方案，共用串行运行锁，各自保存签到状态：

| 渠道 | 运行方式 | 掉线恢复 | 完成后 |
| --- | --- | --- | --- |
| 龙湖天街微信小程序 | Windows 微信窗口控件与截图匹配 | 微信快捷登录及原手机号授权 | 点击右上角圆点图标退出小程序 |
| 龙湖 Android APP | USB 调试授权后的 ADB 页面操作 | 短信登录，验证码需用户提供 | 返回手机桌面并熄屏 |

每天北京时间 **06:00—07:25 随机启动，07:30 截止**。缺失登录、未知页面、验证码挑战、账号变化或签到结果不明时停止。签到状态分别核实，奖励按页面实际文字记录：成长值与珑珠不同，日历上的未来奖励不算今日到账。

本项目为独立工具，非龙湖官方产品。没有免风控保证；请按服务规则和自己的账号权限使用。仅操作界面，不提取登录令牌或调用签到接口，也不解决滑块、图片验证码等安全验证。

2026-10-14 修复：首次打开微信小程序后，最多等待120秒确认页面加载，仍受北京时间7:30截止时间保护；页面未确认时不执行签到。

## 环境

- Windows 10/11，Python 3.9 或以上。
- 小程序：桌面解锁，Windows 微信已登录，可打开“龙湖天街”。
- APP：Android 手机已安装、登录龙湖 APP；USB 数据线持续连接电脑，USB 调试已授权，手机联网。
- 手机按需唤醒模式要求屏幕锁定为“无”或“滑动”。屏幕锁定有密码时停止；程序不修改锁屏密码。

建议采用专用备用机。电脑应保持开机并登录 Windows；小程序需要桌面可操作，APP 不依赖 Windows 桌面解锁。

界面选择器按龙湖 APP 1.29.2 的一次实际界面校准；其他版本或不同界面需要人工核实。图片匹配失败时不要直接降低阈值去强行点击。

## 首次配置

```powershell
python -m pip install -r requirements.txt
python setup_local.py
```

配置向导会复制两个 `config.example.json` 为本地 `config.json`，设置会员昵称和手机号的一致性哈希。手机号只在 Android 目录通过 Windows 用户 DPAPI 加密保存，输入不显示。**所有配置默认禁用，不继承作者的账号、设备、授权或运行状态。**

协议确认需要你自己先阅读对应服务界面的条款。向导仅在你明确选择允许后，才开启后续同类登录页面的自动确认；默认关闭。

### 小程序

1. 在微信中打开“龙湖天街”，从官方菜单创建桌面快捷方式。
2. 将你自己的快捷方式复制到 `longfor-wechat-checkin/龙湖天街.lnk`。仓库不提供别人账号生成的快捷方式。
3. 编译本机窗口程序：

```powershell
cd longfor-wechat-checkin
.\build.ps1
python checkin.py verify-navigation
```

确认账号、会员页和两张“赚珑珠”卡片。需要一次真实签到验证时，先在本地配置中将 `enabled` 改为 `true`，再执行 `python checkin.py manual`。手动模式会签到，请勿把它用于仅查看页面。

### Android APP

1. 从 [Google 官方页面](https://developer.android.com/tools/releases/platform-tools)下载 Windows Platform Tools，将其解压为 `longfor-android-checkin/tools/platform-tools/adb.exe`。
2. 手机开启 USB 调试并允许电脑；完成 APP 首次登录。
3. 在本地 Android `config.json` 的 `device_serial` 填入 `adb devices` 显示的目标设备编号。不要将该文件提交到 Git。

```powershell
cd longfor-android-checkin
python device_probe.py
python checkin.py verify-navigation
```

确认账号、唤醒、返回桌面和熄屏。将本地 `enabled` 改为 `true` 后，可用 `python checkin.py manual` 做一次真实签到验证。默认模板只覆盖已校准界面，页面变化时会停止。

### 短信登录

APP 掉线时会填入加密保存的原手机号，每天最多自动请求一次验证码，然后提示用户继续登录。终端中使用隐藏输入完成：

```powershell
python login_sms.py complete
```

如果你需要主动请求验证码：

```powershell
python login_sms.py request
```

程序也接受从标准输入传入的 JSON，供可信的本机调用方使用。不要把真实手机号或验证码写进脚本、命令历史或提交记录。验证码不写入签到配置、状态文件或日志。

登录成功后核实原会员账号；如果仍在签到时间内且没有当日提交记录，则继续签到。超时只恢复登录。短信验证码仍需用户提供，短信登录恢复不能完全无人值守。

## 启用计划任务

真实验证完成、配置 `enabled=true` 后，在对应目录执行：

```powershell
.\install-task.ps1 -Enable
```

公开版默认任务名为 `Longfor-UI-WeChat-Daily-Checkin` 和 `Longfor-UI-Android-Daily-Checkin`。可通过 `-TaskPrefix` 自定义前缀，`-PythonPath` 指定解释器；已有其他来源同名任务时拒绝覆盖。

任务不会在错过窗口后白天补签。两个渠道全程串行，等待另一渠道结束时仍检查截止时间。签到前保存提交意图，结果不明时当天不重复提交；退出失败保留已经核实的奖励。

结果查看各目录的 `最新执行结果.txt`、`state.json` 和 `logs/`。这些都是本地私人运行资料，不属于开源内容。

## 验证与隐私

```powershell
python -m unittest discover -s longfor-wechat-checkin -p "test_*.py"
python -m unittest discover -s longfor-android-checkin -p "test_*.py"
python -m unittest test_privacy.py
git add .
python privacy_check.py
```

两个测试目录分别运行，避免同名 `checkin.py` 被混用。测试仅使用虚构资料，不会连接真实手机或点击微信。静态检查和单元测试通过不等于任意设备的真实签到成功。

发布使用全新 Git 历史。隐私检查读取 **暂存区的实际 Git 对象**，检查提交清单、敏感格式、默认配置及图片尺寸；它不是完整的个人信息识别系统，发布者仍须审阅自己的修改。

更多数据边界见 [PRIVACY.md](PRIVACY.md)。代码使用 [MIT License](LICENSE)。按钮模板为服务界面的短文字按钮裁剪，仅用于识别；龙湖品牌、服务界面和第三方工具的权利仍属于其权利人。

