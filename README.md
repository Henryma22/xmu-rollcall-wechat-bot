# XMU 微信签到机器人懒人包

这个仓库现在只保留微信机器人主线，目标就是一件事：

- 让你把 XMU TronClass 自动签到做成一个微信机器人

机器人支持这些微信命令：

- `/conf`：分步配置账号
- `/switch 账号ID`
- `/accounts`
- `/answer`
- `/refresh`
- `/cancel`
- `/help`

`/answer` 不会持续轮询 TronClass，而是在你发命令时即时查一次；如果成功，会把签到码或经纬度发回微信。

## 三步上手

### 1. 初始化

```bash
git clone <你的仓库地址>
cd xmu-rollcall-helper
bash scripts/bootstrap.sh
```

### 2. 先扫码登录一次

```bash
bash scripts/start-local.sh --login-only
```

看到登录链接后，用机器人微信号扫码。

### 3. 装成后台服务

```bash
sudo bash scripts/install-systemd.sh
```

看日志：

```bash
sudo journalctl -u xmu-wechatbot -f
```

## 微信里怎么用

首次配置：

1. 发送 `/conf`
2. 按提示发送学号
3. 按提示发送密码
4. 发送 `/answer`

如果配置多个账号：

- 发送 `/accounts` 看账号列表
- 发送 `/switch 2` 切换到 `ID=2`

## 仓库里给你准备好的东西

- [scripts/bootstrap.sh](scripts/bootstrap.sh)：一键建虚拟环境并安装
- [scripts/start-local.sh](scripts/start-local.sh)：本地启动或首次扫码
- [scripts/install-systemd.sh](scripts/install-systemd.sh)：一键安装 `systemd`
- [pfp.jpg](pfp.jpg)：机器人微信号头像素材

## 机器人头像

机器人头像不是 SDK 里设置的，而是“登录这个机器人的微信号本身的头像”。

做法很简单：

1. 用手机或 PC 微信登录机器人微信号
2. 把 [pfp.jpg](pfp.jpg) 设置成头像
3. 再给机器人扫码登录

## 目录说明

运行后会自动生成：

- `.lazybot/xmu-wechatbot.env`：运行环境变量
- `.lazybot/data/`：微信凭证、XMU 登录缓存和账号映射
- `xmu-rollcall-cli/.venv/`：项目虚拟环境

## 高级手动部署

如果你不想用懒人脚本，也可以看手动版：

- [docs/ubuntu-wechatbot-deploy.md](docs/ubuntu-wechatbot-deploy.md)
