# 临时邮箱接口要求

这份文档不是某个具体邮箱服务的使用手册，而是本项目对“临时邮箱服务”这一段的对接契约。

如果你想把自己的邮箱系统接进 `grok-register`，只要实现这里约定的接口，控制台和注册执行器就能直接工作。

如果你用的是 DuckMail：

- 不需要自己再实现这里这套 `/admin/new_address` / `/api/mails` / `/api/mail/<id>` 契约
- 当前仓库已经原生支持 DuckMail 官方接口，直接把 `temp_mail_api_base` 配成 `https://api.duckmail.sbs` 即可
- `temp_mail_domain` 可留空自动选公开域名；`temp_mail_admin_password` 只有在私有域名场景下才需要填 DuckMail API Key

## 一句话说明

当前执行器实际依赖 3 个接口：

- 创建邮箱地址
- 列出收件箱邮件
- 获取单封邮件详情

控制台里的这些字段会参与这段链路：

- `temp_mail_api_base`
- `temp_mail_admin_password`
- `temp_mail_domain`
- `temp_mail_site_password`
- `proxy`

## 当前代码实际调用的接口

执行器实现见 [email_register.py](/home/codex/grok-register/email_register.py)。

自定义 Temp Mail 默认约定如下：

### 1. 创建邮箱

- 方法：`POST`
- 路径：`/admin/new_address`
- 认证头：`x-admin-auth: <temp_mail_admin_password>`
- 可选站点头：`x-custom-auth: <temp_mail_site_password>`

请求体：

```json
{
  "name": "abc123xyz",
  "domain": "mail.example.com",
  "enablePrefix": false
}
```

字段说明：

- `name`：邮箱前缀，执行器会自动随机生成
- `domain`：必须等于你在配置里填的 `temp_mail_domain`
- `enablePrefix`：当前固定传 `false`

成功响应要求至少包含：

```json
{
  "address": "abc123xyz@mail.example.com",
  "jwt": "<mail_token>",
  "password": "optional-mail-password"
}
```

最关键的是：

- `address`
- `jwt`

其中：

- `address` 会被填到 `x.ai` 注册页
- `jwt` 会被后续收件箱轮询接口当作 Bearer Token 使用

### 2. 列出邮件

- 方法：`GET`
- 路径：`/api/mails`
- 认证头：`Authorization: Bearer <jwt>`
- 可选站点头：`x-custom-auth: <temp_mail_site_password>`
- 查询参数：
  - `limit=20`
  - `offset=0`

成功响应可以是下面任意一种结构：

```json
{
  "results": [
    {
      "id": "msg_001",
      "subject": "Your verification code is MM0-SF3"
    }
  ]
}
```

或者：

```json
{
  "data": [
    {
      "id": "msg_001",
      "subject": "Your verification code is MM0-SF3"
    }
  ]
}
```

要求：

- 返回值必须是对象
- 邮件列表字段必须叫 `results` 或 `data`
- 每封邮件至少要有 `id`

### 3. 获取邮件详情

- 方法：`GET`
- 路径：`/api/mail/<msg_id>`
- 认证头：`Authorization: Bearer <jwt>`
- 可选站点头：`x-custom-auth: <temp_mail_site_password>`

成功响应建议返回：

```json
{
  "id": "msg_001",
  "subject": "Your verification code is MM0-SF3",
  "text": "Your verification code is MM0-SF3",
  "html": "<p>Your verification code is <b>MM0-SF3</b></p>",
  "raw": "optional full raw message"
}
```

执行器会从这些字段里依次提取内容：

- `subject`
- `text`
- `html`
- `raw`
- `source`

所以你至少保证其中一个字段里能拿到验证码正文。

## 验证码内容要求

当前执行器主要支持两类验证码：

- `MM0-SF3` 这种 `3位-3位` 字母数字混合格式
- `123456` 这种 6 位数字格式

如果你的邮件详情里能把验证码放进：

- `subject`
- `text`
- `html`

任意一个字段，通常就够了。

## 认证要求

当前实现支持两层认证：

### 管理认证

用于创建邮箱：

```http
x-admin-auth: <temp_mail_admin_password>
```

### 站点级认证

如果你的邮箱 API 还需要额外鉴权，可以启用：

```http
x-custom-auth: <temp_mail_site_password>
```

如果你的服务不需要这层认证，`temp_mail_site_password` 留空即可。

## 状态码建议

建议按下面的方式返回，方便排障：

- `200`：成功
- `400`：请求参数错误
- `401` / `403`：鉴权失败
- `404`：邮件不存在
- `429`：限流
- `500`：服务内部错误

## 与控制台配置的对应关系

- `temp_mail_api_base`
  - 邮箱服务根地址，例如 `https://mail-api.example.com`
- `temp_mail_admin_password`
  - 创建邮箱时放在 `x-admin-auth` 头里的管理口令
- `temp_mail_domain`
  - 实际注册时使用的邮箱后缀
- `temp_mail_site_password`
  - 可选的站点级鉴权口令，对应 `x-custom-auth`
- `proxy`
  - 普通 HTTP 请求代理，主要给邮箱 API 请求用

## 最小可用实现标准

如果你只想先快速打通链路，你的邮箱服务至少满足下面 4 条：

- 能创建一个真实可收信的邮箱地址
- 能返回一个后续可用的 `jwt`
- 能列出最近收到的邮件并提供 `id`
- 能在邮件详情里返回包含验证码的正文

只要满足这 4 条，这个项目就能跑到验证码阶段并继续往下。

## 健康检查页和这里的关系

控制台里的“环境健康检查”页目前对临时邮箱只做基础连通性检查：

- 它会访问 `temp_mail_api_base`
- 它不会真的创建邮箱
- 它也不会真的轮询验证码

所以：

- 健康检查显示“Temp Mail API 正常”只代表接口地址可达
- 不代表你的邮箱域名一定能被 `x.ai` 接受
- 也不代表邮件一定能及时到达

真正验证邮箱链路是否可用，还是要跑一次 `count=1` 的实际注册任务。

## 推荐自测顺序

在正式跑批前，建议按这个顺序测：

1. 先在控制台“环境健康检查”里确认 `Temp Mail API` 可达
2. 用 curl 手工测试创建邮箱接口
3. 给这个邮箱发一封测试邮件
4. 手工测试 `/api/mails` 和 `/api/mail/<id>` 是否能读到验证码正文
5. 再回到控制台跑一个 `count=1` 的验证任务

## curl 示例

### 创建邮箱

```bash
curl -X POST "https://mail-api.example.com/admin/new_address" \
  -H "Content-Type: application/json" \
  -H "x-admin-auth: <admin_password>" \
  -H "x-custom-auth: <site_password>" \
  -d '{
    "name": "demo001",
    "domain": "mail.example.com",
    "enablePrefix": false
  }'
```

### 列出邮件

```bash
curl "https://mail-api.example.com/api/mails?limit=20&offset=0" \
  -H "Authorization: Bearer <jwt>" \
  -H "x-custom-auth: <site_password>"
```

### 获取邮件详情

```bash
curl "https://mail-api.example.com/api/mail/msg_001" \
  -H "Authorization: Bearer <jwt>" \
  -H "x-custom-auth: <site_password>"
```
