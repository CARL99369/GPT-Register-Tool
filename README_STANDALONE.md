<img width="2436" height="1281" alt="image" src="https://github.com/user-attachments/assets/5b08cc03-af0c-4269-9ffa-b39a82747f03" />

# 直绑独立服务

该目录是独立的 5601 服务：

- 不读取外部项目数据库
- 不导入外部项目目录
- 自带 HTTP 核心、配置、静态页面和测试
- 首次启动自动创建本地 `.venv`

## 启动

推荐直接运行 SmsWorkbench，在左侧“支付管理”中点击“直绑支付”。桌面端会自动启动服务，并使用 WebView2 在主窗口内显示页面。

以下脚本保留用于独立运行和维护：

前台启动：

```bat
start_standalone.cmd
```

后台启动：

```bat
run_direct.vbs
```

重启：

```bat
restart_standalone.cmd
```

页面：`http://127.0.0.1:5601/`

## 配置

编辑 `standalone_config.json`：

- `country` / `currency`：提链区域与币种
- `bind_country` / `bind_currency`：绑卡区域与币种
- `promo_campaign`：优惠活动
- `timeout`：HTTP 任务超时
- `billing`：账单资料默认值

服务端不会把 AT、PaymentMethod 或代理池写入数据库。页面会将当前账号列表和代理草稿保存在浏览器本地存储；发布前清理迁移会自动删除旧私有版本的数据。

## API

- `GET /health`
- `GET /api/standalone/info`
- `POST /api/card-bind/session`
- `POST /api/standalone-flow/quick-checkout`
- `GET /api/standalone-flow/task/{task_id}`
- `POST /api/standalone-flow/tasks/clear`

## 测试

```bat
python -m unittest discover -s tests -v
```

## 免责声明

本项目仅用于技术研究、接口调试、自动化测试和个人开发。使用者应确保对所使用的账号、支付方式、代理、访问凭证、接口及网络环境拥有相应使用权限，并遵守所在地法律法规、平台服务条款及相关第三方规则。

严禁将本项目用于未经许可的支付操作、欺诈、盗刷、批量滥用、规避平台限制、侵犯他人权益或其他不当活动。因使用、修改、分发或部署本项目产生的账号限制、资金损失、数据泄露、服务中断、争议或责任，均由使用者自行承担。

本项目按“现状”提供，不承诺功能完整性、稳定性、准确性、适用性或持续可用性。作者及贡献者不对任何直接或间接损失承担责任。下载、安装、运行或使用本项目，即表示使用者已阅读并同意本免责声明。
