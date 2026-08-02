# NinePlus Server

NinePlus Server 是 NinePlus iOS App 的 FastAPI 后端。它通过独立安装的 `ninecli` 读取真实九号车辆、状态、电池和行程数据，再转换为 NinePlus 客户端协议。

生产模式不会在真实数据失败时返回模拟车辆、模拟电池、`TEST-RIDE` 或模拟轨迹。项目不包含九号 Token、账号密码或本地 ninecli 配置。

## 运行环境

- Windows 11
- Python 3.11 或更高版本
- 已独立安装并登录的 `ninecli==0.1.7`

## 安装

在 PowerShell 中进入服务器目录：

```powershell
cd D:\Codex\Nineplus\nineplus-server
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv`、`tokens.json` 和 `ninebot-config` 已被 Git 忽略。

## 环境变量

### `NINEPLUS_API_KEY`

生产环境必填。除 `/healthz` 外，所有接口都要求：

```text
Authorization: Bearer <NINEPLUS_API_KEY>
```

不要将真实 Key 写入源码、README、Git 或聊天记录。可以在本机生成并仅保存在当前 PowerShell 会话：

```powershell
$env:NINEPLUS_API_KEY = (& .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))")
```

在 NinePlus iOS 设置页填写相同的 Key。

### `TEST_MODE`

默认值为 `false`。

```powershell
$env:TEST_MODE = "false"
```

- `false`：只允许真实九号数据或历史真实缓存；没有可用数据时返回 HTTP 503。
- `true`：仅用于本地协议开发，允许现有 TEST 模拟数据 fallback。

Beta 或日常使用环境必须保持 `TEST_MODE=false`。

## 启动

在设置环境变量的同一个 PowerShell 窗口执行：

```powershell
cd D:\Codex\Nineplus\nineplus-server
$env:TEST_MODE = "false"
.\.venv\Scripts\python.exe main.py
```

服务监听：

```text
0.0.0.0:19009
```

环境变量在进程启动时读取；修改 Key 或模式后需要重启服务器。

## 测试

健康检查不需要鉴权：

```powershell
curl.exe -i http://127.0.0.1:19009/healthz
```

业务接口不带 Header 时应返回 HTTP 401：

```powershell
curl.exe -i http://127.0.0.1:19009/vehicles
```

使用当前 PowerShell 中的 API Key：

```powershell
curl.exe -i -H "Authorization: Bearer $env:NINEPLUS_API_KEY" http://127.0.0.1:19009/vehicles
```

检查 Python 语法：

```powershell
.\.venv\Scripts\python.exe -m compileall -q main.py adapters services
```

## 缓存与失败策略

| 数据 | TTL |
| --- | ---: |
| 车辆列表 | 5 分钟 |
| Dashboard 状态 | 45 秒 |
| Battery | 5 分钟 |
| Travel / Travel Detail | 10 分钟 |

真实响应会附加缓存元数据：

- `source=ninebot`：本次数据来自实时九号请求。
- `source=cache`：本次数据来自内存缓存。
- `updated_at`：这份真实数据实际采集成功的 UTC 时间。
- `stale=false`：实时数据或 TTL 内缓存。
- `stale=true`：实时请求失败后返回的过期真实缓存。

真实请求失败时优先返回最近一次成功的真实缓存。没有缓存时返回 HTTP 503。Dashboard 中只有 Travel 允许单独降级：Travel 无数据时 Dashboard 主体仍返回 200，并标记 `available=false` 和 `error=ninebot_travel_unavailable`。

## 安全

- 九号 Token 继续由独立的 ninecli 配置目录管理。
- 不要把 19009 端口转发到公网。
- Windows 防火墙只允许专用网络和本地子网。
- 日志只记录请求时间、SN、接口、结果、数据来源和 stale 状态，不记录 Token、密码或 API Key。
