# ACP Services — 身份支持范围与能力声明（Identity Support Scope）

**日期**：2026-10-19 · **性质**：能力/选型决策台账（tracked），供 todo 52（Lab init/doctor）与 todo 75 消费。
**实现驻点**：`src/acp_services/workspace/identity_lifecycle.py`（B09-6 / plan todo 73）。
**上游**：主方案 §22.1/§22.3/§24.1/§25.2（MU-02/04/13）、评审修订 B6、[组件 Manifest](ACP_Services_Component_Manifest.md) §0/§4。

## 0. 结论摘要（供 doctor 直接引用）

| 能力 | 状态 | 说明 |
|---|---|---|
| Key-only 认证（`local-single`） | **可用** | 仅 API Key；无交互登录、无服务端会话、无邀请/注册、无 first-admin bootstrap |
| 账户邀请 / 建立（`lab`/`hosted`，能力齐备时） | **可用** | 一次性邀请 token（digest-only），接受时由调用方设定口令 |
| 登录 / 会话（`lab`/`hosted`，能力齐备时） | **可用** | 口令（stdlib scrypt/PBKDF2）+ digest-only 会话 token，`session_epoch` 失效 |
| 首次管理员 bootstrap | **可用**（仅 account/session 档） | **无通用默认口令**；已有管理账户即拒绝重放 |
| 禁用 / 撤销（即时生效） | **可用** | 新命令/下载拒绝；既有 SSE 事件流立即关闭；会话 + Key 立即撤销 |
| 恢复（disable/revoke → active） | **可用** | 提升 `session_epoch`；**旧会话/旧 Key 永不复活** |
| **OIDC / SSO** | **不可用（NOT available）** | 见 §3 |

**硬性声明**：`IdentitySupportScope.oidc_supported` 恒为 `False`，任何 doctor/能力报告**不得**将 OIDC 标记为可用或“已验证”。未实现即为不可用（出自评测 B6 与主方案 §25.1）。

## 1. 两个正交轴（不得混用）

- `deployment_tier` ∈ `local-single` / `lab` / `hosted` — 授权/隔离边界，**唯一**决定身份支持范围。
- `execution_mode` ∈ `local` / `lsf` / `slurm` / `podman` — 任务在哪运行；**被记录，永不用来授予身份**。远端执行不隐含多人托管（与 `contracts.access.resolve_scope` 同口径）。

`IdentitySupportScope.to_dict()` 同时输出两轴；测试 `test_deployment_tier_and_execution_mode_are_separate_axes` 守护。

## 2. 档位支持矩阵

| 档位 | 所需能力（`boundary.TierCapability`） | 模式 | enabled | 会话/邀请/bootstrap | OIDC |
|---|---|---|---|---|---|
| `local-single` | 无 | `key-only` | ✅ | ❌（显式声明不支持） | ❌ |
| `lab` | `authentication` **且** `isolation` | `account-session` | 能力齐备才 ✅ | ✅ | ❌ |
| `hosted` | `authentication` **且** `isolation` | `account-session` | 能力齐备才 ✅ | ✅ | ❌ |

- **缺认证/隔离能力 → 拒绝启用**：`identity_support_scope(tier).enabled is False`，`error_code="tier_capability_missing"` 并列出缺失能力；`IdentityLifecyclePolicy.require_enabled()` 抛 `TierCapabilityError`，且每个生命周期操作先调用它（不可静默降级）。
- 该判定复用 `acp_services.workspace.boundary` 的 tier 能力门，不新增第二套判定。

## 3. OIDC 决策（本次迭代）

- 组件 Manifest §0：`pwdlib` / `Authlib` / `PyCasbin` / `Keycloak` 全部为 `candidate`，`approved` 数量为 **0**（[Manifest §0](ACP_Services_Component_Manifest.md)）。按 §28.3“批准规则”，未 `approved` 不得进入依赖清单、不得宣称“已验证”。
- 因此本轮 **不交付 OIDC 网络登录**：无 adapter、无回调、无 `issuer+subject` 网络校验。契约层面仅保证身份以 `issuer+subject` 为重键（todo 9 已交付）；OIDC adapter 属后续（需先按组件策略完成 PoC/批准，见 Manifest 门控 §4 行“todo 52/73 身份”）。
- `source/acp_services/workspace/identity_lifecycle.py` 常量 `OIDC_SUPPORTED = False`；`IdentitySupportScope.limitations` 固定包含 `OIDC_UNAVAILABLE_REASON`。
- **doctor 合同**：todo 52 的 doctor/能力报告对 OIDC 只允许三种三态结论之一——`NOT_VERIFIED`（最严格）或 `FAIL`/`UNAVAILABLE`；**禁止 PASS**。

## 4. 认证与凭据实现

- **口令**：`hashlib.scrypt`（`n=2^15, r=8, p=1`，per-account 16B salt）；无 scrypt 时回退 `PBKDF2-HMAC-SHA256`（600k）。存储仅 digest + 参数；`hmac.compare_digest` 比对。参数随行持久化，跨进程可验证。
- **口令策略**：最小长度 12；拒绝 `WEAK_PASSWORDS` 通用默认口令集；拒绝口令等于登录名。**不存在任何“默认口令”代码路径**。
- **会话/邀请 token**：`acps_<id>.<secret>` / `acpi_<id>.<secret>`，secret 为 256-bit CSPRNG；仅 digest 落库，明文仅返回一次。
- **bootstrap**：已有任一管理账户（非 REVOKED）即拒绝 `bootstrap_already_done`；口令由调用方提供并过策略。
- **无自动合并/升权**：账户仅按 `login_id` 查找（**绝不**按 email）；角色是显式事实，仅经 `Role.ALL` 校验后写入；tier 永不授予角色（复用 `tier_roles()==()` 口径）。

## 5. 撤销语义（即时生效）

`disable_account` / `revoke_account` 原子完成：
1. 提升账户 `session_epoch`（所有既有会话立即 stale）；
2. 撤销该账户**全部**存活会话（`identity_sessions.revoked_at`）；
3. 撤销**全部**登记的 Key 引用（`identity_credential_refs`），并调用注入的 `ApiKeyManager.revoke()`；
4. 审计行（actor/object/before-status/after-status/reason/timestamp）；
5. 事务外关闭该 principal 的全部既有 SSE 流（`close_principal_streams`，reason=`permission_changed`）。

- `authorize_entry` / `authorize_command` / `authorize_download`：DISABLED → `account_disabled`（403）；REVOKED → `account_revoked`（403）；INVITED → `invitation_pending`（403）。未知入口 fail-closed（`unknown_entry`）。
- `recover_account`（DISABLED 或 REVOKED → ACTIVE）提升 epoch；旧会话/旧 Key 保持撤销，永不复活。

## 6. Key-only 支持范围及限制（`local-single` 显式声明）

- 认证 = API Key（B01-3 原语）；**无**交互登录/会话、**无**邀请/注册、**无** first-admin bootstrap。
- `login()` 返回 `sessions_not_supported`（不伪造占位会话）；`invite_account()` 抛 `EnrollmentNotSupportedError`；`bootstrap_first_admin()` 抛 `BootstrapRefusedError`。
- 未知 principal 在该档默认放行（保留可信本地行为）；`authorize_entry` 对已知但非 ACTIVE 账户仍然拒绝。
- 本地多项目（todo 7）不受影响：`resolve_scope` 对本地 principal 的多 project 授权为 `ALLOW`（回归测试守护）。

## 7. 消费方

| 消费方 | 读取 | 要求 |
|---|---|---|
| todo 52 Lab init/doctor | `IdentitySupportScope.to_dict()` | OIDC 只能 `NOT_VERIFIED`/`FAIL`/`UNAVAILABLE`；缺能力档位 `enabled=False` 时拒绝启用对应 Lab 能力 |
| todo 75 组合验收 | 支持矩阵 | Lab 身份功能的组合以本轮交付为准；未选 Lab 能力不阻塞首批本地组合 |
| 运行时授权 | `authorize_entry` / `stream_authorizer` | 禁用/撤销即时生效，不保留有效会话/Key |

<!-- SERVICES_STATUS_SYNC:todo70 -->
## 实施状态同步（2026-10-19 / todo 70）

- **delivered:** `workspace/{identity_lifecycle,identity_mapping,keys,entry_authz,boundary,admin}.py` 的账户/Key/授权/管理面组件；`OIDC_SUPPORTED=False` 为显式能力声明。
- **verified:** 模块级 261 passed（`test_services_api_keys.py`、`test_services_identity_*.py`、`test_services_entry_authz.py`、`test_services_admin.py`，closure L1-06）。
- **NOT_VERIFIED:** Lab 身份 HTTP/UI 接线（closure L2-12）与 OIDC 网络登录（L3-09）；组件已交付不等于已接入。
- **limits:** `local-single` 路径保持；缺能力档位 `enabled=False` 时拒绝启用对应 Lab 能力，不静默降级为匿名/无配额。
- **authority:** 状态以 `docs/reference/current/ACP_Services_Closure_20261019.md` 为准（L2-12/L3-09 `NOT_VERIFIED`）。
