# Admin route to permission map

This document captures the admin authorization model introduced with database-backed admin sessions.  Basic auth is retained only for emergency use when `admin_basic_fallback_enabled=true`.

| Route pattern | Permission |
| --- | --- |
| `GET /admin`, dashboard and analytics overview | `dashboard.read` |
| `GET /admin/users*`, user detail, user activity | `users.read` |
| `GET /admin/live`, `GET /admin/api/live/messages` | `conversations.read` |
| `GET /admin/media`, media message APIs, generated media downloads | `media.read` |
| `GET /admin/receipts`, financial reports | `payments.read` |
| Payment approval/rejection and subscription mutation POSTs | `payments.mutate` |
| Wallet/coin balance changes and bulk coin gifts | `wallets.adjust` / `coin_gifts.manage` |
| Add-on grant/revoke/toggle | `addons.manage` |
| Memory reset and digest actions | `memories.manage` |
| Relationship reset and style operations | `relationship.manage` |
| Image generation retry and visual profile reset | `generated_media.manage` |
| Proactive operations | `proactive.manage` |
| Non-financial settings/model operations | `settings.nonfinancial` |
| Health/log pages | `health.read` |
| `GET/POST /admin/admin-users*` | `admin_users.manage` |

Owner has all permissions. Finance receives financial/payment/wallet permissions. Support receives user, conversation, media, memory, relationship, and support permissions. Operator receives generated-media, health, non-financial settings, and proactive permissions. Viewer receives read-only permissions.
