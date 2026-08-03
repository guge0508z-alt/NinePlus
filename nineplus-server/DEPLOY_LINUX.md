# NinePlus Server：Ubuntu 24.04 部署

本文档用于把 NinePlus Server 部署到腾讯云 Ubuntu 24.04 LTS，并通过 systemd 长期运行。以下命令默认仓库放在 `/opt/NinePlus`，服务用户为 `nineplus`。

## 全链路联调状态（2026-08-03）

NinePlus 已完成腾讯云 Ubuntu 环境的全链路联调，验证结果如下：

- NinePlus Server 已在腾讯云 Ubuntu 上通过 systemd 稳定运行。
- iOS App 可以通过公网 HTTP 访问服务端，ATS 测试配置已验证生效。
- NinePlus API Key 的 Bearer 鉴权正常，未授权请求会被拒绝。
- `GET /vehicles`、Dashboard、Battery 和 Travel 数据链路均已验证正常。
- 本次联调仍使用 HTTP，仅用于当前测试阶段；正式公网使用仍建议迁移到 HTTPS。

验证记录不包含公网 IP、API Key、九号账号、Token 或其他敏感信息。

## 1. 部署前说明

- 推荐 Python 3.12（Ubuntu 24.04 默认版本）。
- `ninecli==0.1.7` 提供 Linux x86-64 和 ARM64 wheel，可由 `pip` 直接安装。
- 除 `/healthz` 外，所有 NinePlus API 都需要 `Authorization: Bearer <NINEPLUS_API_KEY>`。
- 生产环境必须使用 `TEST_MODE=false`。
- 九号 Token 只保存在 `/var/lib/nineplus-server/ninebot-config/tokens.json`，不要提交到 Git。
- 不要在聊天、命令历史、日志或截图中暴露 API Key、九号密码或 Token。

## 2. 安装系统软件

登录 Ubuntu 后执行：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl
python3 --version
```

## 3. 创建服务用户和目录

```bash
sudo useradd --system --home /var/lib/nineplus-server --create-home --shell /usr/sbin/nologin nineplus
sudo install -d -o nineplus -g nineplus -m 750 /opt/NinePlus
sudo install -d -o nineplus -g nineplus -m 700 /var/lib/nineplus-server/ninebot-config
sudo install -d -o root -g nineplus -m 750 /etc/nineplus-server
```

如果 `nineplus` 用户已经存在，`useradd` 会提示已存在，可以继续后续步骤。

## 4. 获取代码

```bash
sudo -u nineplus git clone https://github.com/guge0508z-alt/NinePlus.git /opt/NinePlus
cd /opt/NinePlus/nineplus-server
```

如果目录中已经有仓库，改用：

```bash
cd /opt/NinePlus
sudo -u nineplus git pull --ff-only origin main
cd nineplus-server
```

## 5. 一键安装 Python 环境

`install.sh` 会创建 `.venv`、安装 `requirements.txt`（包括 `ninecli==0.1.7`）、初始化 ninecli 配置目录并检查 Python 语法。

```bash
cd /opt/NinePlus/nineplus-server
sudo -u nineplus env \
  NINEBOT_CLI_CONFIG=/var/lib/nineplus-server/ninebot-config \
  bash ./install.sh
```

确认 ninecli 可以运行：

```bash
sudo -u nineplus /opt/NinePlus/nineplus-server/.venv/bin/python -m pip show ninecli
sudo -u nineplus /opt/NinePlus/nineplus-server/.venv/bin/python -m ninecli --help
```

## 6. 登录 ninecli

不要把账号密码直接写进命令或脚本。先用隐藏输入读入当前 Shell 变量，再调用 ninecli；Shell 历史只会保存变量名，不会保存实际密码：

```bash
read -r -p 'Ninebot account: ' NINEBOT_ACCOUNT
read -r -s -p 'Ninebot password: ' NINEBOT_PASSWORD
echo
sudo -u nineplus /opt/NinePlus/nineplus-server/.venv/bin/python -m ninecli \
  --config /var/lib/nineplus-server/ninebot-config \
  login -u "${NINEBOT_ACCOUNT}" -p "${NINEBOT_PASSWORD}"
unset NINEBOT_ACCOUNT NINEBOT_PASSWORD
```

登录参数会在 ninecli 进程运行期间短暂出现在本机进程参数中，因此请只在你独占管理的服务器维护窗口执行。完成后确认变量已经 `unset`。

登录成功后只检查文件是否存在，不要输出文件内容：

```bash
sudo -u nineplus test -f /var/lib/nineplus-server/ninebot-config/tokens.json
echo $?
```

输出 `0` 表示 Token 文件存在。随后执行只读车辆列表验证：

```bash
sudo -u nineplus /opt/NinePlus/nineplus-server/.venv/bin/python -m ninecli \
  --config /var/lib/nineplus-server/ninebot-config \
  vehicles
```

## 7. 配置环境变量

先生成随机 API Key。请在自己的 SSH 终端中执行，不要把输出发送到聊天：

```bash
/opt/NinePlus/nineplus-server/.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

创建 `/etc/nineplus-server/environment`：

```bash
sudoedit /etc/nineplus-server/environment
```

填写以下内容，并把占位值替换为刚生成的 Key：

```text
TEST_MODE=false
NINEPLUS_API_KEY=在这里填写随机生成的Key
NINEBOT_CLI_PYTHON=/opt/NinePlus/nineplus-server/.venv/bin/python
NINEBOT_CLI_CONFIG=/var/lib/nineplus-server/ninebot-config
```

限制配置文件权限：

```bash
sudo chown root:nineplus /etc/nineplus-server/environment
sudo chmod 640 /etc/nineplus-server/environment
```

systemd 的 `EnvironmentFile` 不执行 Shell 表达式，因此必须填写最终值，不能填写 `$VARIABLE` 或命令替换。

## 8. 手动启动验证

先以前台方式启动，确认代码和环境变量正常：

```bash
cd /opt/NinePlus/nineplus-server
sudo -u nineplus bash -c 'set -a; source /etc/nineplus-server/environment; set +a; exec .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 19009'
```

另开一个 SSH 终端测试无需鉴权的健康检查：

```bash
curl -i http://127.0.0.1:19009/healthz
```

预期 HTTP 200。按 `Ctrl+C` 停止前台服务。

不要直接在命令行写真实 API Key。可以从仅 root 可读的环境文件加载后测试：

```bash
sudo bash -c 'set -a; source /etc/nineplus-server/environment; set +a; curl -i -H "Authorization: Bearer ${NINEPLUS_API_KEY}" http://127.0.0.1:19009/vehicles'
```

## 9. 安装 systemd 服务

```bash
sudo cp /opt/NinePlus/nineplus-server/nineplus-server.service /etc/systemd/system/nineplus-server.service
sudo systemctl daemon-reload
sudo systemctl enable --now nineplus-server
```

检查状态和日志：

```bash
sudo systemctl status nineplus-server --no-pager
sudo journalctl -u nineplus-server -n 100 --no-pager
```

重启、停止和启动：

```bash
sudo systemctl restart nineplus-server
sudo systemctl stop nineplus-server
sudo systemctl start nineplus-server
```

## 10. 防火墙与腾讯云安全组

如果只允许固定公网 IP 访问，优先在腾讯云安全组中把 TCP 19009 来源限制为你的家庭公网 IP。不要对 `0.0.0.0/0` 完全开放。

使用 UFW 时可以限制来源：

```bash
sudo ufw allow OpenSSH
sudo ufw allow from 你的公网IP to any port 19009 proto tcp
sudo ufw enable
sudo ufw status
```

如果公网 IP 经常变化，建议后续使用 HTTPS 反向代理、VPN 或 Tailscale，而不是直接暴露 HTTP 19009。API Key 不能替代 HTTPS；明文 HTTP 经过公网时 Header 可能被窃听。

## 11. 更新服务

```bash
sudo systemctl stop nineplus-server
cd /opt/NinePlus
sudo -u nineplus git pull --ff-only origin main
cd nineplus-server
sudo -u nineplus env NINEBOT_CLI_CONFIG=/var/lib/nineplus-server/ninebot-config bash ./install.sh
sudo systemctl start nineplus-server
sudo systemctl status nineplus-server --no-pager
```

更新不会删除 `/var/lib/nineplus-server/ninebot-config` 中的 Token。

## 12. 常见问题

### 返回 HTTP 401

确认 iOS 中的 NinePlus API Key 与 `/etc/nineplus-server/environment` 的 `NINEPLUS_API_KEY` 完全相同，然后重启服务。

### 返回 HTTP 503

检查服务日志、ninecli Token 和只读车辆列表：

```bash
sudo journalctl -u nineplus-server -n 100 --no-pager
sudo -u nineplus /opt/NinePlus/nineplus-server/.venv/bin/python -m ninecli \
  --config /var/lib/nineplus-server/ninebot-config \
  vehicles
```

不要通过启用 `TEST_MODE` 掩盖生产环境错误。

### 修改环境变量后没有生效

环境变量只在进程启动时读取：

```bash
sudo systemctl restart nineplus-server
```
