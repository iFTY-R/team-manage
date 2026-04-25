# Team 导入 JSON 示例

本文档存放管理员页面 Team 导入功能支持的 JSON 示例，便于后续删除 `demo/1.md` 后继续保留参考样例。

## 当前支持的 JSON 结构

- **CPA 格式**
- **cockpit-tools 格式**

当前仅支持以上两类结构，不支持通用任意 JSON 映射。

---

## 1) CPA 格式

```json
{
  "id_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6Ii...<snip>",
  "access_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6Ii...<snip>",
  "refresh_token": "rt_xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "account_id": "c4b01a90-ebce-4013-8db6-6f3cce32c1ca",
  "last_refresh": "2026-04-25T12:47:33.610Z",
  "email": "example@163.com",
  "type": "codex",
  "expired": "2026-05-05T12:29:31.000Z"
}
```

### 字段提取规则

- `access_token` → Access Token
- `refresh_token` → Refresh Token
- `account_id` → Account ID
- `email` → 邮箱
- `client_id` / `session_token` 若存在也会参与导入

---

## 2) cockpit-tools 格式

```json
[
  {
    "id": "codex_xxxxxxxxxxxxxxxxxxxxxxxx",
    "email": "example@qq.com",
    "auth_mode": "oauth",
    "api_provider_mode": "openai_builtin",
    "user_id": "user-xxxxxxxxxxxxxxxx",
    "plan_type": "team",
    "account_id": "c4b01a90-ebce-4013-8db6-6f3cce32c1ca",
    "account_name": "example-workspace",
    "account_structure": "workspace",
    "tokens": {
      "id_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6Ii...<snip>",
      "access_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6Ii...<snip>",
      "refresh_token": "rt_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    }
  }
]
```

### 字段提取规则

- `tokens.access_token` → Access Token
- `tokens.refresh_token` → Refresh Token
- `account_id` → Account ID
- `email` → 邮箱
- `tokens.client_id` / `tokens.session_token` 若存在也会参与导入

---

## 导入限制

- 仅支持 **对象** 或 **对象数组**
- 当前仅内置支持 **CPA** 与 **cockpit-tools** 两类结构
- 不支持的 JSON 结构会返回明确错误
- Excel / CSV 不在当前 JSON 导入支持范围内
