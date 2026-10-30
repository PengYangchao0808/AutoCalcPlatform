# ruff: noqa: E501
"""Versioned agent guidance content pack (plan 2026-10-30 §4/§13/§14).

The pack is pure data: no I/O, no service state, no science facts of its own.
Every entry carries an id, a version, the workflows it covers and a coverage
label; the per-workflow prompts are the reviewed drafts from the unified plan
(``ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md`` §13-§14) published as a
versioned, installable content package.  Parameters, defaults and enums are
*not* duplicated here — they are projected from the submit contracts by
``acp.api.workflow_contracts``; a guide never becomes a second parameter
authority.

Coverage labels are honest by construction:

* ``first_batch`` — one of the four composite first-batch scenarios
  (Confsearch / BatchOptimize / PESsearch / scan);
* ``documented_partial`` — the workflow is described and constrained, but its
  MCP consumption entry may not be closed; the guide says so explicitly.

The pack is exposed read-only through ``GET /api/v1/guidance`` (HTTP) and the
``acp_get_guidance`` MCP tool; both return the same versioned payloads with a
stable sha256 digest for caching (plan §4.2: version change, contract mismatch
or a new section requirement forces a re-read).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from acp.catalog import WORKFLOW_CATALOG

logger = logging.getLogger(__name__)

__all__ = [
    "GUIDE_FORMAT",
    "MAX_GUIDE_BYTES",
    "MAX_INDEX_CARD_BYTES",
    "PACK_VERSION",
    "GuideEntry",
    "audit_pack",
    "get_guide",
    "guide_ids",
    "guidance_index",
    "read_guide",
    "workflow_guide_map",
]

#: Wire format of one guide payload (frozen for caching compatibility).
GUIDE_FORMAT = "acp.guidance.v1"

#: Pack-level version.  Bump when any entry changes; entry digests make cache
#: invalidation per-guide precise.  1.1.0: BatchOptimize role wire-syntax +
#: PESsearch unit/bond-side vocabulary (DA validation round,
#: reports/acp2_mcp_guidance_20261030).  1.2.0: method-selection ladder and
#: artifact-locating topics (agent feedback round 2).
PACK_VERSION = "1.2.0"

#: Byte budgets (plan §4.2): UTF-8 bytes, not tokens.  A guide that would
#: exceed a caller-supplied bound is returned with explicit omissions — never
#: silently truncated.
MAX_GUIDE_BYTES = 8192
MAX_INDEX_CARD_BYTES = 1024

#: First-batch composite scenarios (plan §1.2/§9).
FIRST_BATCH_WORKFLOWS: tuple[str, ...] = ("Confsearch", "BatchOptimize", "PESsearch", "scan")


@dataclass(frozen=True)
class GuideEntry:
    """One versioned guidance document.

    Attributes:
        guide_id: Stable id, e.g. ``workflow.PESsearch`` or ``topic.recovery``.
        kind: ``shared`` | ``workflow`` | ``orchestration`` | ``topic``.
        title: Human-readable short title.
        version: Entry version (semver-ish string).
        workflows: Workflow ids this entry covers (workflow kind only).
        coverage: ``first_batch`` or ``documented_partial``.
        summary: One-line purpose for discovery cards.
        sections: Ordered mapping section name -> body text.  The body is
            guidance, not a parameter schema and never an execution authority.
        topics: Referenced topic ids (one level of indirection only, plan §4.2).
    """

    guide_id: str
    kind: str
    title: str
    version: str
    workflows: tuple[str, ...] = ()
    coverage: str = "documented_partial"
    summary: str = ""
    sections: Mapping[str, str] = field(default_factory=dict)
    topics: tuple[str, ...] = ()


def _entry_digest(entry: GuideEntry) -> str:
    """Stable sha256 over the semantic content (id/version/sections/workflows)."""
    material = {
        "guide_id": entry.guide_id,
        "version": entry.version,
        "workflows": list(entry.workflows),
        "sections": dict(entry.sections),
    }
    canonical = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _entry_size_bytes(entry: GuideEntry) -> int:
    return sum(len(text.encode("utf-8")) for text in entry.sections.values())


# ---------------------------------------------------------------------------
# Shared execution prompt (plan §13.2)
# ---------------------------------------------------------------------------

_SHARED_EXECUTION = GuideEntry(
    guide_id="shared.execution",
    kind="shared",
    title="通用执行 Prompt（各工作流共用）",
    version="1.0.0",
    summary="所有工作流共用的执行约束：契约优先、复用工件、授权内直行、证据分级。",
    coverage="first_batch",
    sections={
        "body": (
            "你通过 ACP MCP 协助用户完成受控的计算化学任务。使用当前会话实际开放的工具和服务端契约，"
            "不以本指南代替执行权限、科学验证或参数校验。\n"
            "1. 发现协议（按此三步，不要靠读平台源码猜接口）：①acp_capabilities() 读 workflow_contracts "
            "索引——每个工作流给出 coverage 与 {tool, arguments} 详情指针及未契约化时的 fallback；②"
            "acp_capabilities(workflow=<id>) 读该工作流完整 input/method/resources schema、示例与 constraints；"
            "③acp_get_guidance(guide_id) 读操作指南。区分 registered、当前可执行、参数覆盖完整与真实验收范围；"
            "coverage=not_provided 时按 fallback 行动（用 CLI 路径或报告缺口），不读源码不猜字段。\n"
            "2. 优先复用已存在的结构、任务和工件。核对项目、来源 attempt、generation/digest 与电子态；"
            "保留原子顺序和映射。不要猜绝对路径、手填摘要或把展示序号当原子/帧身份。\n"
            "3. 从已有记录或授权读取科学选择；真正缺少且影响计算含义的信息集中询问。参数默认值必须来自契约；"
            "方法升级、改变电荷/电子态、扩大研究范围不能悄悄进行。\n"
            "4. 全自动模式下，授权范围内的选点、发布、提交、等待、读结果与有限修订直接推进。权限不足、来源不明、"
            "超范围或预算不足时停止相关写入并说明原因；不自行批准自己的计划。\n"
            "5. 提交前使用实际开放的预检入口，读取错误与生效配置。对明确可修正的输入错误按字段/rule 修正；"
            "不靠轮换字段、关校验或改变科学目标获取通过。\n"
            "6. 使用平台规定的幂等机制。相同请求重试复用原提交意图和键；回包丢失先找回已有任务。"
            "输入或计划变化时重新预检并绑定新身份。\n"
            "7. 对长任务使用有界等待或建议的查询间隔。区分 MCP 调用成功与任务 completed，"
            "区分整个任务和逐 item/stage 的结果。\n"
            "8. 从 status/结果发现获得产物描述，再显式 prepare/read。检查引用、编码、完整性、截断与摘要作用域；"
            "看不到的部分不推断为通过。\n"
            "9. 读取优化/SCF/方法/结构等实际证据。区分 requested、effective 和 executed；模型解释不是执行事实。"
            "缺失验证保留 unknown/inconclusive，证伪保留 refuted。\n"
            "10. PES/scan 的 TS/INT 选点只产生初猜；初猜必须通过合法发布进入 BatchOptimize，再读取逐候选结果。"
            "保存成功不是 TS 找到，Batch completed 也不是反应路径验证完成。\n"
            "11. 资源选择：提交前读 acp_capabilities 的 capacity（准入视角的当前可用配额，"
            "非 OS 空闲），nproc/mem 取 available 之下且不超过 admission.limits 的值；不要凭空猜核心数。\n"
            "12. 只对有依据、在范围内且预算允许的失败项或缺失信息追加计算；限制总候选数与补算轮次。\n"
            "13. 会话恢复先查询平台记录，核对已有任务、选点和提交意图，再决定下一步。\n\n"
            "对用户的结果回答包括：实际完成的工作、关键结果及引用、可支持的结论、缺失/失败项、下一步或停止原因。"
        ),
    },
    topics=(
        "topic.inputs_electronic_state",
        "topic.method_energy_comparability",
        "topic.stationary_point_verification",
        "topic.partial_failure_recovery",
    ),
)


def _workflow_entry(
    guide_id: str,
    title: str,
    summary: str,
    purpose: str,
    steps: str,
    *,
    coverage: str,
    topics: tuple[str, ...] = (),
    version: str = "1.0.0",
) -> GuideEntry:
    return GuideEntry(
        guide_id=guide_id,
        kind="workflow",
        title=title,
        version=version,
        workflows=(guide_id.split(".", 1)[1],),
        coverage=coverage,
        summary=summary,
        sections={"purpose": purpose, "steps": steps},
        topics=topics,
    )


# ---------------------------------------------------------------------------
# Workflow guides (plan §13.3-§13.17)
# ---------------------------------------------------------------------------

_CONFSEARCH = _workflow_entry(
    "workflow.Confsearch",
    "Confsearch — 构象搜索与筛选",
    "按协议生成构象并筛选/精修，产出有来源、有质量说明的构象集合。",
    (
        "围绕给定分子生成构象并按所选协议筛选/精修。现有协议 xtb-crest、xtb-md、censo-crest、xtbmd-censo；"
        "阶段依赖由协议决定。screen 不选取后续精修候选；纯 xTB 协议不能宣称执行了 DFT 精修。"
        "构象搜索完成不证明全局最低点或构象空间穷尽。"
    ),
    (
        "当前工作流：Confsearch。目标是获得有来源、有质量说明的构象集合。\n"
        "1. 核对分子结构、立体化学、电荷/多重度。已有可信输入时复用；SMILES 映射以服务端物化结果为准。\n"
        "2. 读取当前协议列表与每个协议实际阶段/可用性；不把 registry 的软件并集强加给纯 xTB 场景。\n"
        "3. 绑定筛选与精修策略。screen 允许零精修候选；纯 xTB 路径只报告其实际计算级别；"
        "不提交该工作流不支持的 max_conformers 等参数。\n"
        "4. 预检后提交和等待，分别读取搜索产生、筛选保留、精修选中及实际精修成功数。\n"
        "5. 读取候选结构、能量类别、实际方法和质量记录；精修/未精修混合集合不直接当同级别排序。\n"
        "6. 选择下游候选时说明范围与依据，使用稳定候选引用，保留结构多样性与预算限制。\n"
        "7. 用户要求进一步优化/验证时按候选引用构造 BatchOptimize；只要构象集合时报告筛选范围与局限后结束。\n"
        "8. 阶段失败时区分已有可用集合和未完成部分；只在授权内恢复或补算。\n\n"
        "输出：协议与实际阶段、分阶段计数、候选及来源、方法可比性、覆盖局限、Batch 引用或结束原因。"
    ),
    coverage="first_batch",
)

_BATCHOPTIMIZE = _workflow_entry(
    "workflow.BatchOptimize",
    "BatchOptimize — 批量优化与后续计算",
    "对带角色的结构集合逐项执行配置的阶段（opt/freq/sp/thermo），保留逐 item 来源与执行事实。",
    (
        "对一组带角色（TS/INT）的结构逐项执行配置的阶段。当前 profile 为 opt_only、opt_freq、opt_freq_sp、"
        "opt_freq_sp_thermo。角色生效参数由配置解析器决定，不由名字或 TAG 自动证明科学属性。"
        "PES/scan 初猜必须在本工作流完成所需优化和适用验证；只有 opt_only 不等于完成驻点验证。"
    ),
    (
        "当前工作流：BatchOptimize。将候选初猜推进为可检查的计算结果。\n"
        "1. 读取输入集合与发布状态。PES/scan 初猜的推荐链路：上游 PES review 确认选点后，以 "
        "input.from_artifact（上游 RESULT/result_manifest.json）+ selected_ids（如 pes_ts_frame_005）消费已确认"
        "候选；未确认的推荐点不能直接当最终输入。\n"
        "2. 每个 item 的用途角色必须是 wire 级显式，TS 的两种正确表达：①inline item 携带 role/tag 字段"
        '（role:"ts"）；②from_artifact 候选经 review 确认的角色。关键：未标角色的 inline item 默认按 '
        "INT（最低点优化）执行，method.batch_roles 的 ts 配置不会自动套用到未标角色的 item，当前预检不提示"
        "该失配——提交前逐 item 自查角色标注。TS 表示尝试过渡态优化的初猜，INT 表示相应最低点优化用途。\n"
        "3. 读取 profile 和生效配置；需要频率/单点/热化学时选择包含这些阶段的配置，不把 opt_only 报成 opt_freq。\n"
        "4. 核对每项电荷/多重度、优化/频率/单点级别、温度与资源；遵守 batch_roles 与平铺参数一致性检查。\n"
        "5. 预检、提交与等待；读取逐 item/stage 的成功、失败、跳过及原因。\n"
        "6. 对成功优化结果读取适用频率和模式验证；报告原生判定及缺项。\n"
        "7. 只对可比的物种/方法/能量定义/环境做比较；区分电子能与热化学校正，不重复加校正。\n"
        "8. 失败项先判断可修复原因，再在剩余范围与预算内补算；复用已完成阶段的身份校验机制。\n"
        "9. TS 研究若缺模式关联或 IRC/端点证据，继续适用验证或明确停在候选阶段。\n\n"
        "输出：逐候选来源、角色、实际阶段、结构/结果引用、科学判定与缺项、失败恢复、下一验证任务或停止原因。"
    ),
    coverage="first_batch",
    topics=(
        "topic.stationary_point_verification",
        "topic.method_energy_comparability",
        "topic.partial_failure_recovery",
    ),
    version="1.1.0",
)

_PESSEARCH = _workflow_entry(
    "workflow.PESsearch",
    "PESsearch — 键长势能面探索与初猜生成",
    "键长扫描默认入口：探索曲线、提出可追溯 TS/INT 初猜，交由 BatchOptimize 优化验证。",
    (
        "可从结构或上游结果进入扫描；direct bond-length scan 请求包含来源、坐标及协议，执行松弛扫描、"
        "配置的逐帧单点与候选推荐。部分 strategy 名称只参与校验/元数据，不能将 direct_ts 等名称当作自主 "
        "TS 搜索已交付。矩阵反应位点识别算法待接入；首版使用用户提供或确认的位点，缺失时询问。"
        "推荐与最终发布分开，选点仅为初猜。"
    ),
    (
        "当前工作流：PESsearch。键长势能面扫描优先采用此入口。\n"
        "1. 核对用户给定或确认的反应假设、关键原子及结构映射；缺少关键位点则询问，不假称已运行自动位点识别。\n"
        "2. 读取当前可执行的扫描模式和请求契约；核对原子索引约定（0-based）、坐标类型/单位（合法 unit："
        "distance 用 angstrom、angle/dihedral 用 degree；非法值当前要到执行层才失败，提交前自查）、范围/点数"
        "及多坐标关系；double_bond_scan 类选择要求扫描原子对为感知键——从产物侧拉伸已成键对，勿从反应物侧"
        "压缩非键对。\n"
        "3. 绑定扫描方法与逐帧能量方法、电子态、资源和预算。大体系例外须记录理由，不自行按原子数拍阈值。\n"
        "4. 执行后先读曲线概况和全部点覆盖状态，再读候选区间的完整邻域点表及必要结构；区分扫描能与单点能，"
        "保留失败点、缺点和不收敛标记。\n"
        "5. 结合目标结构变化、局部曲线和质量信息选择多个有理由的 TS/INT 初猜或明确不选；不得只挑全局最高点。\n"
        "6. 信息不足时提出有限补算区间/坐标与预算，和已有帧初猜提案分开；新点在真实计算完成前不具有帧身份。\n"
        "7. 按真实来源保存提案；全自动授权有效时自动发布，交互模式交由用户审查；缺少自动发布能力时报告未开放，"
        "不借 manual 来源绕过。\n"
        "8. 发布后将初猜连同角色和来源绑定送入 BatchOptimize；保存成功而 Batch 未运行时只报告初猜已保存/待计算。\n"
        "9. 读取 Batch 逐项结果，按适用规则继续模式与端点验证，或保留失败、不确定与停止原因。\n\n"
        "输出：坐标/方法/覆盖、所选初猜与证据、补算记录、发布 revision、Batch 引用及验证进展。"
    ),
    coverage="first_batch",
    topics=(
        "topic.selection_supplement",
        "topic.stationary_point_verification",
        "topic.partial_failure_recovery",
    ),
    version="1.1.0",
)

_SCAN = _workflow_entry(
    "workflow.scan",
    "scan — 指定坐标扫描",
    "用户显式选择或确有适用理由的内坐标扫描；ScanTS 显式 opt-in，扫描结束不等于找到驻点。",
    (
        "执行契约支持的内坐标扫描（distance/angle/dihedral），获得逐点结构与能量；公开说明以 ORCA relaxed "
        "scan 为主。ScanTS 默认关闭。键长任务默认推荐 PESsearch；scan 用于明确选择或已确认限制的例外。"
        "scan 与 PES 共享底层能力不代表上层候选契约相同。"
    ),
    (
        "当前工作流：scan。执行用户明确选择或确有适用理由的坐标扫描。\n"
        "1. 对键长扫描先检查是否应使用默认 PESsearch；用户明确选 scan 或有已确认限制时记录原因并继续。\n"
        "2. 读取 scan 契约，确认坐标类型、原子顺序/索引约定、单位、起止/点数、多坐标耦合、方法和电子态；"
        "坐标校验要求可确认的原子数（提交显式几何），未开放的维度或后端不猜参数绕过。\n"
        "3. ScanTS 只在明确选择、授权且平台支持时启用；没有启用时不得暗示内部已优化 TS。\n"
        "4. 预检后执行，逐点检查结构可读性、收敛/失败状态和能量可比性；缺失点不插值成已计算事实。\n"
        "5. 目标只是扫描时提供曲线范围、原始结果与局限；目标包含驻点搜索时按编排指南选初猜、必要时在范围内补算。\n"
        "6. 通过 scan 对应的通用帧候选契约发布，不能伪造 PES review；写入口尚未开放时明确说明阻塞。\n"
        "7. 将合法发布的初猜传入 BatchOptimize，并跟踪逐候选优化/频率等结果。\n\n"
        "输出：选择 scan 的原因、坐标与方法、点覆盖及异常、帧引用、初猜/Batch 关联或纯扫描结束理由。"
    ),
    coverage="first_batch",
    topics=("topic.selection_supplement",),
)

_SINGLEPOINT = _workflow_entry(
    "workflow.singlepoint",
    "singlepoint — 固定几何单点能",
    "在给定几何与电子态上计算电子能等产物；不优化几何、不算热化学。",
    "在给定几何上通过当前工作流的 ORCA 路径计算电子能等所需产物。输入的几何与电子态是科学身份的一部分。",
    (
        "当前工作流：singlepoint。\n"
        "1. 绑定确切结构引用与来源；确认用户要求的是当前几何单点。\n"
        "2. 从契约确认方法、基组或复合方法规则、环境、电荷/多重度和资源；不为带内置基组的方法随意填另一个基组。\n"
        "3. 预检有效配置后提交，检查实际 SCF/电子态质量、执行方法、能量类型和产物完整性。\n"
        "4. 比较结果前核对组成、电子态、方法和能量定义等适用条件；不同化学计量的反应能需明确平衡关系。\n"
        "5. 报告电子能及单位和来源，不把它称为自由能；需要热化学或构象权重时引用相应产物或提出后续任务。\n"
        "6. 未收敛时按明确原因与授权修订，不输出不可信数值作为成功结果。\n\n"
        "输出：结构/方法/电子态、能量及引用、收敛与质量、比较适用范围和后续需求。"
    ),
    coverage="documented_partial",
    topics=("topic.method_energy_comparability",),
)

_OPTIMIZE = _workflow_entry(
    "workflow.optimize",
    "optimize — 几何优化",
    "普通 ORCA 几何优化入口；TS 初猜的批量推进优先走 BatchOptimize 角色配置。",
    "普通 ORCA 几何优化入口；是否存在约束、实际优化语义按当前契约，不能凭方法名称推断。",
    (
        "当前工作流：optimize。\n"
        "1. 确认结构来源、电子态、方法和环境，以及有无约束；属于已发布 PES/scan 初猜的 TS/INT 集合时走 "
        "BatchOptimize 链。\n"
        "2. 读取允许的优化配置与有效默认值，预检后执行；不用未公开 raw keyword 绕过契约。\n"
        "3. 读取实际优化收敛、SCF 质量、最终几何和轨迹；出现组成/目标结构变化时记录并按目标判断。\n"
        "4. 优化收敛不证明局部或全局最低点；需要驻点性质时继续适用 frequency。\n"
        "5. 有约束的结果保留约束说明；解除约束属于新的明确计算选择。\n"
        "6. 失败只在范围内执行有依据的恢复。\n\n"
        "输出：输入和最终结构引用、实际设置、收敛/变化、驻点验证缺项及下一步。"
    ),
    coverage="documented_partial",
)

_FREQUENCY = _workflow_entry(
    "workflow.frequency",
    "frequency — 频率与振动分析",
    "在给定结构和方法上运行振动频率计算并发布频率、模式/Hessian 产物；不是优化入口。",
    "在给定结构和方法上运行 ORCA 振动频率计算并发布频率、模式/Hessian 等实际产物；不保证自动给出适用于任意比较的自由能。",
    (
        "当前工作流：frequency。\n"
        "1. 核对几何来源、是否优化及相关约束、电子态、计算方法与所需环境；未优化结构可以按明确目的计算，"
        "但不能暗示它已经是驻点。\n"
        "2. 预检并执行，检查结果完整性、频率/模式对应关系和已有质量判定。\n"
        "3. 对最低点/TS 目标使用平台或已确认的科学判据，不自造统一虚频阈值。\n"
        "4. 目标 TS 不能只凭一个虚频判定正确；需检查模式对应的结构变化，并按需要进一步 IRC/端点关联。\n"
        "5. 热化学量只在实际生成且条件明确时报告；区分电子能与校正项，避免重复累计。\n\n"
        "输出：来源结构/方法、频率和模式引用、质量/判定依据、热化学适用范围、需要的修订或验证。"
    ),
    coverage="documented_partial",
    topics=("topic.stationary_point_verification",),
)

_XTB_OPTIMIZE = _workflow_entry(
    "workflow.xtb_optimize",
    "xtb_optimize — xTB 快速几何优化",
    "GFN-xTB 优化入口，适合授权的预优化/初筛；不等于 DFT 层级结果。",
    "GFN-xTB 等当前支持配置的优化入口。原生约束请求与可选物理单点由专用 XtbConstrainedOpt 入口处理。",
    (
        "当前工作流：xtb_optimize。\n"
        "1. 读取支持的方法/环境/资源和电子态表达，确认用户允许 xTB 级别；不因它快就替代已授权的 DFT 目标。\n"
        "2. 绑定输入结构，使用服务端校验电荷、多重度和所需自旋参数；不为 xTB 硬填 DFT 基组。\n"
        "3. 预检并执行，检查实际收敛、几何变化和能量类型。\n"
        "4. 作为预优化时，将实际最终结构和来源传给后续工作流，保留 xTB 与后续级别的区别。\n"
        "5. 需要专用原生约束/物理单点能力时先发现 XtbConstrainedOpt 是否公开可执行。\n"
        "6. 不将一次快速优化声称为构象搜索完成、全局最低点或 DFT 验证。\n\n"
        "输出：xTB 设置、收敛和结构引用、能量定义、后续用途与限制。"
    ),
    coverage="documented_partial",
)

_IRC = _workflow_entry(
    "workflow.irc",
    "irc — 从 TS 候选追踪反应路径",
    "从明确 TS 来源的结构运行 IRC，读取各方向轨迹与端点信息；方向符号不自动代表反应物/产物。",
    "从具有明确过渡态来源的结构运行 IRC，读取实际完成的方向/轨迹与端点信息。已知 TS 的路径验证不同于自主发现机理。",
    (
        "当前工作流：irc。\n"
        "1. 核对 TS 几何、input role 与 provenance、实际频率/模式证据和所需 Hessian 等输入；没有证据时不伪造 "
        "TS 来源。\n"
        "2. 来自 PES/scan 的初猜先取得 BatchOptimize 后的相应结构和验证产物。\n"
        "3. 明确目标反应、预期端点、原子映射、方法/电子态、要追踪的方向与资源范围。\n"
        "4. 预检后执行，分别读取各方向进度、终止原因、轨迹与端点；单方向失败不能写成双向连接已确认。\n"
        "5. 读取平台端点分类与结构关联证据；IRC 末帧不自动等同独立确认的最低点。\n"
        "6. 端点与目标不符则记录 refuted 或未定结论，保留轨迹。\n\n"
        "输出：TS 来源、各方向轨迹/状态、端点证据与映射、目标关联结论和剩余验证。"
    ),
    coverage="documented_partial",
    topics=("topic.stationary_point_verification",),
)

_TSMODE = _workflow_entry(
    "workflow.tsmode",
    "tsmode — 基于已有虚频模式的定向 OptTS",
    "消费已有频率来源 bundle，沿选择的虚频模式执行定向 OptTS 并按实际配置做最终频率验证。",
    "消费已有频率来源 bundle，验证 Hessian/几何与模式映射，沿选择的虚频模式执行定向 OptTS。不同于对任意结构凭空指定一个模式。",
    (
        "当前工作流：tsmode。\n"
        "1. 获取实际 source bundle，检查源 output、Hessian、几何、方法/电子态及映射状态；必须引用真实来源。\n"
        "2. 读取模式表与振动信息，依据目标结构变化选 source_mode_index；模式原始索引不能用图中排序号替代。\n"
        "3. 不启用 allow_unverified_mapping 绕过源映射问题；公共 MCP 尚不能消费该 bundle 时报告缺失入口。\n"
        "4. 读取优化设置、重试上限及 final_frequency 的生效值，预检后执行。\n"
        "5. 检查优化轨迹、最终结构、频率与模式是否满足目标用途。\n"
        "6. 对源 Hessian 不匹配、模式不可用、失败或质量不足，按原因停止/修订。\n"
        "7. 得到候选后按需求进入 IRC/端点验证。\n\n"
        "输出：源 bundle/模式引用、映射状态、实际优化与验证结果、候选用途和后续验证。"
    ),
    coverage="documented_partial",
)

_CASSCF = _workflow_entry(
    "workflow.casscf",
    "casscf — CASSCF / 可选 NEVPT2 单点",
    "ORCA 多参考单点；活性空间、目标态与初猜是科学选择，不从原子数机械猜出。",
    "按实际配置执行 CASSCF 和可选的 NEVPT2 处理。完成检查包括真实 CAS 收敛及请求产物完整性。",
    (
        "当前工作流：casscf。\n"
        "1. 读取结构、电子态、活性电子/轨道数、轨道选择或初猜来源、目标根/态平均设置、方法/基组和所需 "
        "NEVPT2 类型。\n"
        "2. 关键活性空间或态选择缺失时集中向用户请求；不把模型推测当成确定设置。\n"
        "3. 明确这是给定几何单点；不宣称内部自动完成几何优化。\n"
        "4. 预检实际支持范围并执行；读取 CAS 收敛事实、请求产物、能量类型和实际目标态。\n"
        "5. 只在确实请求并成功得到对应产物时报告 NEVPT2 结果，区分 CASSCF 能量与后处理能量。\n"
        "6. 比较多个结构/态前确认活性空间和状态定义可比较。\n\n"
        "输出：活性空间/态/初猜来源、实际方法、收敛及产物证据、能量和适用局限。"
    ),
    coverage="documented_partial",
)

_NMR = _workflow_entry(
    "workflow.nmr",
    "nmr — NMR 预测、归属与候选比较",
    "候选输入与实验谱处理：构象/筛选/GIAO/平均/归属缩放与 DP4/DP5 分析；统计最高值不是无条件结构证明。",
    "候选输入和实验谱处理，经配置的构象生成/筛选、GIAO 屏蔽、Boltzmann/等价核平均、归属/缩放和 DP4/DP5 分析。"
    "具体协议与资产可用性必须发现。",
    (
        "当前工作流：nmr。\n"
        "1. 核对候选结构/立体化学、实验核种与峰值/单位、溶剂/温度、归属状态及缺失信息；不虚构实验峰或候选集合。\n"
        "2. 读取可用协议、误差模型与必要资产/软件；缺资产不能静默输出另一种分析却保持原标签。\n"
        "3. 预检后运行，逐候选检查构象集合、计算成功集合、平均权重实际覆盖和缺失核/峰；部分失败必须报告。\n"
        "4. 使用平台给出的归属、缩放、证据门和统计结果；区分 DP4 的比较语义与 DP5 的实际计算模式。\n"
        "5. 报告模型模式、fallback/诊断和敏感性标记；requires_review 或证据不齐不能隐藏。\n"
        "6. 修改峰值/归属后优先使用已公开且适用的分析修订入口。\n\n"
        "输出：候选与实验版本、实际计算/分析协议、覆盖/缺失、匹配和统计引用、局限与下一步。"
    ),
    coverage="documented_partial",
)

_XTBPATH = _workflow_entry(
    "workflow.XtbPathSearch",
    "XtbPathSearch — 冻结请求的 xTB PATH 探索",
    "消费 pes2ts_xtb_path_request_v1 执行 xTB PATH；本地专用，路径高点不是已验证 TS。",
    "消费冻结请求，经任务层执行 xTB PATH 并发布路径帧/profile。配方由上游拥有，适配器验证和转发而不补科学默认值；"
    "当前为本地专用，不在远程允许集。",
    (
        "当前工作流：XtbPathSearch。\n"
        "1. 确认当前会话确实开放此工作流及冻结请求消费方式；当前本地专用，不能自动投向 LSF。\n"
        "2. 获取真实 pes2ts_xtb_path_request_v1；起终点、原子顺序/映射、电子态、path.inp/recipe、种子、资源与预算"
        "由已认可上游或明确用户方案提供。\n"
        "3. 不根据两张结构图片自行补完整 PATH 配方；缺少冻结内容则报告缺项。\n"
        "4. 校验端点身份、原子映射与请求版本，预检后执行。\n"
        "5. 读取真实路径帧、能量定义、profile 和失败/质量信息；只有实际存在的帧才能用于选初猜。\n"
        "6. 路径高点不是已验证 TS；若目标继续寻找驻点，检查该来源是否支持合法候选发布与 Batch 适配。\n\n"
        "输出：冻结请求来源、端点映射、路径/质量引用、初猜与下游适配状态。"
    ),
    coverage="documented_partial",
)

_ORCAGRADIENT = _workflow_entry(
    "workflow.OrcaGradient",
    "OrcaGradient — 单几何能量梯度",
    "消费 pes2ts_orca_gradient_request_v1 执行 ORCA EnGrad；梯度是 dE/dX（Hartree/bohr），不等于力。",
    "在冻结几何与配置上得到可被上游算法消费的梯度证据。梯度当前产物定义为 Hartree/bohr；只有具备受审查的转换与"
    "坐标绑定时才能用于相应算法。当前本地专用。",
    (
        "当前工作流：OrcaGradient。\n"
        "1. 确认本地入口、公开请求 schema 及当前授权，获取真实 pes2ts_orca_gradient_request_v1。\n"
        "2. 绑定几何、原子顺序、电荷/多重度、方法/基组和请求片段；不擅自改变上游算法指定的计算设置。\n"
        "3. 预检并执行，检查实际能量、梯度维度、有限数值、单位和原子对应；缺失梯度不填零或由模型估计。\n"
        "4. 保持 dE/dX 的语义和符号；不把返回数组直接称为力。\n"
        "5. 本工作流不自动优化结构、不执行完整 TS 搜索、不提供 Hessian。\n"
        "6. 将结果和来源交回已授权上游算法。\n\n"
        "输出：请求/结构引用、实际方法、梯度定义/单位/质量、能量及适用消费者。"
    ),
    coverage="documented_partial",
)

_XTBCONSTRAINED = _workflow_entry(
    "workflow.XtbConstrainedOpt",
    "XtbConstrainedOpt — 原生约束优化与可选无约束单点",
    "消费 pes2ts_xtb_constrained_opt_request_v1；正常退出与几何收敛分开，约束总能与物理单点能区分。",
    "在单个 xTB 进程内执行约束优化，可选在最终几何上做无约束物理单点。约束优化总能含 restraint potential，"
    "必须与 physical_sp 能量区分。当前本地专用。",
    (
        "当前工作流：XtbConstrainedOpt。\n"
        "1. 确认本地支持和冻结请求消费入口，获取真实 pes2ts_xtb_constrained_opt_request_v1；核对原子顺序、"
        "约束/固定原子、目标值、电子态、方法和资源。\n"
        "2. 检查上游指定的 recipe 与 physical_sp 选择；需要物理单点能时明确请求，不将约束总能改名为物理能。\n"
        "3. 预检后执行；不由 harness 在外部接管每步梯度迭代或改写约束。\n"
        "4. 读取原生 evidence 中的 converged_geometry、最终几何、约束信息和错误；workflow completed 但几何未收敛"
        "时明确记录。\n"
        "5. 分别报告 energy_effective 与实际生成的 energy_physical。\n"
        "6. 约束结构不是无约束驻点；继续研究时按公开适配进入 BatchOptimize 与后续验证。\n\n"
        "输出：冻结约束来源、实际收敛、最终结构、两类能量的存在性/定义、下游用途与局限。"
    ),
    coverage="documented_partial",
)

# ---------------------------------------------------------------------------
# Orchestration prompt (plan §14)
# ---------------------------------------------------------------------------

_ORCHESTRATION = GuideEntry(
    guide_id="orchestration.pes_to_batch",
    kind="orchestration",
    title="全自动初猜选择 → BatchOptimize 跨工作流编排",
    version="1.0.0",
    summary="从扫描/路径证据提出初猜，自动推进 BatchOptimize，并给出有证据边界的研究结果。",
    coverage="first_batch",
    sections={
        "body": (
            "当前目标：在用户授权范围内，从扫描/路径证据提出初猜，自动推进 BatchOptimize。\n"
            "阶段 A：核对任务与执行范围——读取反应假设、用户给定/确认的位点、已存在任务和来源结构；核对服务端"
            "验证的自动执行范围、方法/资源、候选数、补算与重试轮次和剩余预算；键长扫描优先 PESsearch，已有用户"
            "显式选择或已确认例外则使用适用 scan 并记录理由；不重跑已有效完成的扫描。\n"
            "阶段 B：读取并判断已有数据——先获取点覆盖、方法/能量定义、失败/缺失区域和实际帧身份，再读候选区间"
            "邻域和必要结构；根据反应假设、结构变化、局部曲线和质量证据提出初猜，保留多个合理备选；证据不够时"
            "选择读取更多数据、有限补算或停止，不强制选最高点。\n"
            "阶段 C：发布与 Batch 提交——保存带 source task/attempt、profile digest、帧 ID、用途角色、证据/理由"
            "和备选的提案；全自动授权有效时按公开操作发布并记录 agent 来源和平台授权依据，不伪装 manual；以合法"
            "发布的候选和稳定 selection revision 构造 BatchOptimize，读取角色实际配置与 profile；预检并用持久提交"
            "意图提交，提交回包丢失先查询原意图/TaskRef。\n"
            "阶段 D：逐候选验证与停止——读取逐 item/stage 结果，保留从初猜到最终几何的关联；用实际优化、频率、"
            "模式和适用端点证据判断；只有选点/发布成功时状态仍为初猜，只有 Batch 完成时也不能直接声称目标反应 "
            "TS 已验证；后续 IRC/端点验证在范围内且能力可用时继续，缺能力/证据、预算耗尽、全部候选不适用或命题"
            "被证伪时停止并解释。\n"
            "恢复时从任务、选点 revision、提交意图和已完成产物继续；来源 rerun 后不把新帧替换进已冻结的旧 Batch。\n\n"
            "最终报告：探索范围、所选初猜及依据、自动授权/发布记录、Batch 引用和逐候选结果、科学结论及缺项、"
            "实际补算/重试与停止原因。模型理由不计作科学证据。"
        ),
    },
    topics=(
        "topic.selection_supplement",
        "topic.stationary_point_verification",
        "topic.partial_failure_recovery",
    ),
)

# ---------------------------------------------------------------------------
# Shared topics (plan §4.1 common topics; one level of indirection)
# ---------------------------------------------------------------------------

_TOPIC_INPUTS = GuideEntry(
    guide_id="topic.inputs_electronic_state",
    kind="topic",
    title="输入与电子态确认",
    version="1.0.0",
    summary="结构引用、原子映射与电荷/多重度是科学身份的一部分。",
    sections={
        "body": (
            "复用已存在的结构、任务和工件；核对项目、来源 attempt、generation/digest 与电子态，保留原子顺序和"
            "映射。不猜绝对路径、不手填摘要、不把展示序号当原子/帧身份。电荷与多重度必须与电子数一致，由平台"
            "校验；改变电荷/电子态属于新的科学选择，需明确记录。SMILES 等输入的原子映射以服务端物化结果为准。"
        ),
    },
)

_TOPIC_COMPARABILITY = GuideEntry(
    guide_id="topic.method_energy_comparability",
    kind="topic",
    title="方法与能量可比性",
    version="1.0.0",
    summary="只对可比的物种/方法/能量定义/环境做比较。",
    sections={
        "body": (
            "区分扫描能、单点能、电子能与热化学校正，避免重复加校正；不同方法/基组/环境的能量不直接排序；精修/"
            "未精修、xTB/DFT 混合集合不能当同一级别比较。比较反应能前确认化学计量平衡关系与标准态/温度一致。"
            "requested、effective 与 executed 方法身份分别读取；来源不明的值不参与比较。"
        ),
    },
)

_TOPIC_STATIONARY = GuideEntry(
    guide_id="topic.stationary_point_verification",
    kind="topic",
    title="驻点验证",
    version="1.0.0",
    summary="优化收敛不证明驻点性质；TS 判定需要虚频模式对应与端点证据。",
    sections={
        "body": (
            "优化收敛不证明局部最低点，更不证明全局最低点；分类驻点需要频率证据。对 TS 目标：一个虚频只是局部"
            "曲率事实，需检查模式对应的结构变化，并按需要 IRC/端点关联；单方向 IRC 失败或步数耗尽不写成双向连接"
            "已确认。选点/发布成功仍是初猜；Batch completed 不等于反应路径验证完成。缺验证保留 unknown/"
            "inconclusive，证伪保留 refuted。"
        ),
    },
)

_TOPIC_RECOVERY = GuideEntry(
    guide_id="topic.partial_failure_recovery",
    kind="topic",
    title="部分失败与恢复",
    version="1.0.0",
    summary="逐 item/stage 定位失败；只在授权与预算内有依据地补算。",
    sections={
        "body": (
            "区分整个任务与逐 item/stage 的结果；部分失败定位到具体 item/stage 与原因，不整批盲重跑。先判断可修复"
            "原因（输入/收敛/资源/超时），再在剩余范围与预算内补算；复用已完成阶段的身份校验机制，避免相同结构和"
            "配置重复提交。会话恢复先查询平台记录（任务、选点 revision、提交意图），不凭聊天摘要重建；提交回包丢失"
            "先用幂等键查询原 TaskRef，不生成新键。"
        ),
    },
)

_TOPIC_SELECTION = GuideEntry(
    guide_id="topic.selection_supplement",
    kind="topic",
    title="选点与补算",
    version="1.0.0",
    summary="选点只产生初猜；补算是新点提案，不混同已有帧。",
    sections={
        "body": (
            "PES/scan 中选出的 TS/INT 角色表示后续计算用途，不是已验证驻点；允许多个初猜、平坦区间或无可信候选，"
            "不只挑全局最高点。已有帧初猜提案绑定来源 task/attempt、profile digest、帧 ID、用途角色与证据引用；"
            "补算提案针对目标区间/坐标，通过新计划预检与提交后，用真实新帧选择——新点在计算完成前不具有帧身份。"
            "两种提案分开保存，不互相伪装。第一版可复用已有算法推荐作为线索，但推荐不是确认。"
        ),
    },
)

_TOPIC_METHOD_SELECTION = GuideEntry(
    guide_id="topic.method_selection",
    kind="topic",
    title="方法与精度阶梯（泛函/基组选择）",
    version="1.0.0",
    summary="默认 r2SCAN-3c 优化 + wB97X-mV 单点；CCSD(T) 只用于极高精度单点，不做默认。",
    sections={
        "body": (
            "按精度需求选级别，不无脑上高阶方法（高阶方法成本急剧上升且对几何通常无必要）：\n"
            "1. 预筛/构象/初猜：GFN2-xTB（xtb_optimize、Confsearch 纯 xTB 协议、PES scan_optimizer）。\n"
            "2. 常规 DFT（默认档）：几何优化用 r2SCAN-3c（复合方法，自带基组，不填 basis）；单点用 "
            "wB97X-mV 或 wB97X-D4 + def2-TZVP(D)。能量比较与频率验证在此档完成即可满足多数研究。\n"
            "3. 高精度档（仅在关键量需要 ~1 kcal/mol 级精度时）：在 DFT 几何上做 DLPNO-CCSD(T) 单点"
            "（必要时配更大基组/外推）；不做 CCSD(T) 优化，不把 CCSD(T) 当默认或『更保险』的选择。\n"
            "4. 表达：优化级别与单点级别分开填——BatchOptimize 的 batch_roles 中 method=优化方法、"
            "sp_method=单点方法；singlepoint 工作流只算单点级别。\n"
            "5. 同一比较集内级别必须一致；升级级别属于新的科学决定，须记录理由并重新预检。"
        ),
    },
)

_TOPIC_ARTIFACT_LOCATING = GuideEntry(
    guide_id="topic.artifact_locating",
    kind="topic",
    title="计算产物定位与读取",
    version="1.0.0",
    summary="终态 status 自带产物描述符；prepare→read 用五元组；目录名由 molecule_name/task_name 控制。",
    sections={
        "body": (
            "1. 正路是描述符链：作业终态后 acp_status 的返回携带 artifacts 描述符（artifact_id、"
            "generation、digest、size 等）——五元组 (task_id, attempt, artifact_id, generation, digest) "
            "全部来自该描述符与提交回包，不需要猜路径或手填摘要；随后 acp_prepare_artifact 等待 ready，"
            "再 acp_read_artifact 读取（支持 max_bytes 分段与 base64）。\n"
            "2. 目录命名：submit 的 molecule_name / task_name 字段控制 run_root 下的任务目录名；不传时平台"
            "按 mol_<Workflow>__<序号> 命名。多作业并发时建议显式传 molecule_name 以免对照目录。\n"
            "3. 多作业定位：acp_list_jobs（按状态过滤、分页）列出权限范围内任务及其引用；不要遍历文件系统"
            "猜目录。\n"
            "4. 结构帧类产物（PES profile/帧、Batch 结构/频率）优先经描述符读取；MCP 面不暴露宿主绝对路径，"
            "需要落到文件时由用户/客户端在 read 后自行保存。\n"
            "5. 工作脚本与中间几何放持久工作区（如仓库工作目录），不要放 /tmp——会话环境可能被清理。"
        ),
    },
)

_TOPIC_NON_STANDARD = GuideEntry(
    guide_id="topic.non_standard_entries",
    kind="topic",
    title="非普通 active 入口（BoundedRecipe / retired / fake）",
    version="1.0.0",
    summary="遇到非普通入口先读取真实公开状态；不从历史名称推断今天可提交。",
    sections={
        "body": (
            "BoundedRecipe 是额外的 envelope-only 配方执行入口，只执行提交前冻结的依赖和参数，不计为第 16 个普通"
            "工作流。retired 工作流仅历史读取，禁止因旧文档提到 ensemble/energy/optfreq 等名称就提交。fake 仅测试，"
            "custom_sequence 为 planned。不得将文档中的步骤列表当编译器已支持的 DAG；不得为补齐缺失步骤执行任意脚本"
            "或改用 fake；旧任务可只读解释，不能从历史 workflow 名称推断它今天仍能提交。"
        ),
    },
)

_ENTRIES: tuple[GuideEntry, ...] = (
    _SHARED_EXECUTION,
    _CONFSEARCH,
    _BATCHOPTIMIZE,
    _PESSEARCH,
    _SCAN,
    _SINGLEPOINT,
    _OPTIMIZE,
    _FREQUENCY,
    _XTB_OPTIMIZE,
    _IRC,
    _TSMODE,
    _CASSCF,
    _NMR,
    _XTBPATH,
    _ORCAGRADIENT,
    _XTBCONSTRAINED,
    _ORCHESTRATION,
    _TOPIC_INPUTS,
    _TOPIC_COMPARABILITY,
    _TOPIC_STATIONARY,
    _TOPIC_RECOVERY,
    _TOPIC_SELECTION,
    _TOPIC_METHOD_SELECTION,
    _TOPIC_ARTIFACT_LOCATING,
    _TOPIC_NON_STANDARD,
)

_GUIDES: dict[str, GuideEntry] = {entry.guide_id: entry for entry in _ENTRIES}


def guide_ids() -> tuple[str, ...]:
    """All guide ids in pack order."""
    return tuple(_GUIDES)


def get_guide(guide_id: str) -> GuideEntry:
    """Return one entry; raises ``KeyError`` for an unknown id."""
    return _GUIDES[guide_id]


def workflow_guide_map() -> dict[str, str]:
    """Active workflow id -> its workflow guide id (catalog-driven check base)."""
    return {
        entry.workflows[0]: entry.guide_id
        for entry in _ENTRIES
        if entry.kind == "workflow" and entry.workflows
    }


def guidance_index() -> dict[str, Any]:
    """Compact discovery index (plan §4.2: cards, not full bodies)."""
    cards = []
    for entry in _ENTRIES:
        card = {
            "guide_id": entry.guide_id,
            "kind": entry.kind,
            "title": entry.title,
            "version": entry.version,
            "coverage": entry.coverage,
            "summary": entry.summary,
            "digest": _entry_digest(entry),
            "size_bytes": _entry_size_bytes(entry),
            "sections": list(entry.sections),
        }
        if entry.workflows:
            card["workflows"] = list(entry.workflows)
        if entry.topics:
            card["topics"] = list(entry.topics)
        cards.append(card)
    return {
        "format": GUIDE_FORMAT,
        "pack_version": PACK_VERSION,
        "max_guide_bytes": MAX_GUIDE_BYTES,
        "guides": cards,
    }


def read_guide(
    guide_id: str,
    *,
    section: str | None = None,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Read one guide (optionally one section) with explicit omissions.

    Args:
        guide_id: Entry id.
        section: Optional single section name.
        max_bytes: Optional byte bound for the returned body.  When the
            selection would exceed it, the payload degrades to section names
            plus byte sizes with ``"omitted_reason": "budget"`` — never a
            silent truncation (plan §4.2).

    Raises:
        KeyError: Unknown guide id.
        ValueError: Unknown section name.
    """
    entry = _GUIDES[guide_id]
    base = {
        "format": GUIDE_FORMAT,
        "guide_id": entry.guide_id,
        "kind": entry.kind,
        "title": entry.title,
        "version": entry.version,
        "coverage": entry.coverage,
        "summary": entry.summary,
        "digest": _entry_digest(entry),
        "topics": list(entry.topics),
    }
    if entry.workflows:
        base["workflows"] = list(entry.workflows)
    if section is not None and section not in entry.sections:
        raise ValueError(
            f"guide {guide_id} has no section {section!r}; available: {list(entry.sections)}"
        )
    selection = {section: entry.sections[section]} if section else dict(entry.sections)
    body_bytes = sum(len(text.encode("utf-8")) for text in selection.values())
    if max_bytes is not None and body_bytes > int(max_bytes):
        return {
            **base,
            "sections": {},
            "section_sizes": {
                name: len(text.encode("utf-8")) for name, text in entry.sections.items()
            },
            "size_bytes": body_bytes,
            "omitted": list(entry.sections),
            "omitted_reason": "budget",
            "hint": "re-read with a larger max_bytes or per-section reads",
        }
    return {
        **base,
        "sections": selection,
        "size_bytes": body_bytes,
        "omitted": [name for name in entry.sections if name not in selection] or None,
        "omitted_reason": None if section is None or section in selection else "section_filter",
    }


def _active_workflow_ids() -> tuple[str, ...]:
    return tuple(
        str(entry["id"])
        for entry in WORKFLOW_CATALOG
        if entry.get("status", "active") == "active" and entry.get("visible", True)
    )


def audit_pack() -> dict[str, Any]:
    """Machine check material: coverage of catalog active workflows (plan §15.1)."""
    active = _active_workflow_ids()
    covered = set(workflow_guide_map())
    return {
        "active_workflows": list(active),
        "guided_workflows": sorted(covered),
        "missing_guides": sorted(set(active) - covered),
        "extra_guides": sorted(covered - set(active)),
        "pack_version": PACK_VERSION,
    }
