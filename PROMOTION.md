# 深知可信PPT GitHub Public 发布说明

仓库地址：

https://github.com/dylanzhangzx/dknowc-ppt-assistant

## 简介

深知可信PPT（dknowc PPT assistant）是北京彩智科技有限公司旗下“深知可信智能”提供的演示文稿制作 Agent Skill：生成侧采用约束 SVG → 原生 DrawingML 编译路线，主 Agent 逐页手写约束 SVG，确定性编译器导出真实可编辑的 PowerPoint（原生形状/文本/图表/表格，非整页图片）；内容侧完全使用深知可信搜索获取权威、可溯源的政策、数据与案例素材。内置党政简洁、数据图表、商务汇报、庄重典雅、培训课件等风格预设，支持 16:9 / 4:3 / 小红书 / 1:1 / 竖版 / A4 多画布规格，交付 .pptx 与双版可信溯源核验报告（提纲版 + 成稿版）三件套。

本 GitHub Public 版采用 skills.sh 渠道配置，不内置深知搜索 API Key。需要素材检索的任务首次调用时必须先确认环境变量 `DKNOWC_API_KEY` 已配置；未配置时，Agent 可先通过 MaaS 手机号验证码流程获取 Key，让当前任务临时继续执行；持久化环境变量是独立步骤，必须在用户明确同意后再处理。

## GitHub Release 文案

Title:

v1.3.6 - GitHub public release

Body:

This is the skills.sh GitHub public release of 深知可信PPT (dknowc PPT assistant), an Agent Skill that generates genuinely editable native PowerPoint presentations with authoritative, source-linked content.

Highlights (v1.3.6):

- Uses the skills.sh channel configuration.
- v1.3.6 aligns the registration-link contract with the platform: the registration request body field `source` is a contract marker and is sent as `agent` again (a skill identifier in that field made returning users hit an account-exists error and fail to retrieve their key); the `X-Dknowc-Attribution` header key is renamed to `agentSource` (value unchanged, kept distinct from the body contract marker); SMS sending now has a browser-verification branch (`CAPTCHA_REQUIRED` with a validated verification link, distinct exit codes for sent / needs-verification / error) plus client-observation headers, error-code-family messages, and a non-error `pending` state handled with a single same-parameter retry; plaintext keys are returned only when local persistence fails.
- v1.3.6 also includes a wording cleanup across distributed files, a fix to a crash in the registration branch, and a corrected default remark string.

- v1.3.5 applies four fixes from the 2026-10-05 field test feedback: (1) duplicate source cards eliminated by a normalized title+URL dedupe key, with same-article-multi-citation merged into one card listing all citation ids (any id resolves); (2) link-alive checking removed — API-returned links enter the report as-is, archive snapshots remain an additional view entry, `--skip-link-check` kept as a no-op; (3) the report hero now shows the original question when `--question` is passed explicitly; (4) delivery discipline — run deliver_outputs with absolute paths without a preceding cd, and verify the dest_source prefix. Also restores `normalize_snapshot_url()` (accidentally removed with the link-check code) and switches the benefit wording to the points system (100k points on signup + 100k after identity verification).

- v1.3.3 adds call-attribution declaration (`X-Dknowc-Attribution`, `kind=skill;source=...;version=...;channel=...`): a single package-root `attribution.json` plus `scripts/attribution.py` construct the header, and both request-sending scripts (`trusted_search.py`, `register_key.mjs`) attach it so downloads, registrations and calls can be joined per channel. The version is resolved dynamically from SKILL.md.
- v1.3.3 also adds an MCP-first key path: when the host exposes the dknowc workspace MCP with OAuth already granted, the API key is obtained through `create_api_key` and persisted via `register_key.mjs save-key` (key passed on stdin) without asking the user for a phone number; otherwise the flow falls back to the existing SMS registration.
- v1.3.1-v1.3.2 (rolled up): retrieval unified onto the pure-script channel (the MCP retrieval channel was rolled back after unreliable large-result persistence, `mcp_convert.py` kept for future restore), multi-query retrieval now defaults to parallel execution with per-query immediate persistence, business-code validation (50001 is not written to disk and exits 1), JSON key persistence migrated to a dedicated per-machine config file with automatic legacy cleanup, the delivery chain was rewritten (host detection for WorkBuddy/Doubao, never-overwrite with `_v2` renaming, `--dest` guard, `--probe`), and the provenance report gained knowledge-library and source-article terminology, chapter TOC fixes, citation re-ordering, citation-scoped statistics, and versioned output.

- v1.3.0 performs a full redesign of the provenance verification report (aligned with the same-origin plan in the official-document-writing skill 3.7.0): continuous body text flow with inline citation capsules (numbered badge + material title, tap to expand the source card in place); a standalone material-library view (large search, real term-frequency hot words, retrieval-group tabs, cited/uncited filters); per-chapter citation badges with top-bar tools (body-only reading, copy-full-text, print/archive modes); a process-recap bar (retrieval -> ingest -> line-by-line compare -> verified, all real numbers); live link-activity checking (404/410 + soft-404 title sniffing on government sites, conservative pass on connect failures/403) with snapshot fallback (screenShotPath, auto-fills missing /A/ hierarchy) so verification status decouples from link status; multi-block material cards with breadcrumb chains; and a purple visual scheme. New flags `--skip-link-check` and `--disable-snapshot`.

- v1.2.1 hardens the registration funnel into script-driven fixed scripts: `references/onboarding_scripts.md` becomes the canonical phrase library (S1 value pitch / S2 phone-number request / S3 sent / S4 wrong code / S5 success / S6 environment topics, plus error-response table, FAQ and universal prohibitions); `register_key.mjs` and `trusted_search.py` now emit `user_message` payloads agents must relay verbatim (phone numbers masked, quota-exhaustion detection with 402/429 handling and a no-retry rule); `initialize.py` gains `guide_message`/`env_message` and `python_executable`; adds `references/sample_trace_report.html` as a full-featured report sample.

- v1.2.0 applies the 0907-meeting eight-part report redesign: report name unified to 溯源核验报告 with an official-document-style identity header; the five verification metrics renamed to human-readable wording (citation correspondence / material freshness / material composition / pre-delivery checks / current validity); original-passage identity labels with collapse for long excerpts; title chains showing article-section positions; gold high-credibility badges; `recalled_materials` grouping for retrieved-but-unused materials (excluded from citation statistics); search-condition filter pills; and a mobile side-by-side comparison sheet (AI-generated statement vs. original passage).
- v1.2.0 also adds an API-key resolution fallback (`api_key.py`): process environment first, then ~/.zshrc parsing when the host process cannot see shell exports; outline page-plan tables drop the redundant evidence column (citations carry it); table evidence chips render in-cell instead of below the table.

- v1.1.0 upgrades the provenance reports into verification-style reports: a first-screen verification sheet with five real computed metrics (source traceability, citation binding, freshness check, type coverage, self-check), report-style section cards, four-color source taxonomy with filtering, print/archive mode, `--stage outline|final` dual-stage adaptation, and hard validation before generation (refuses to render when materials exist but citations are missing, or citations exist but zero materials are extracted). Mobile-specific adaptations (680px breakpoint, bidirectional anchors, bottom-sheet material popups) included.
- v1.1.0 also adds host-environment delivery (`deliver_outputs.py`, auto-detects WorkBuddy-style host workspaces and copies deliverables there), an SVG slide HTML preview page (`preview_slide_html.py`) for the first-page confirmation gate, and authoring-discipline hardening (no ghost citations; missing source links degrade to gentle notices instead of failing verification).

- Native editable output: pages are hand-authored as constrained SVG and compiled by a deterministic converter into real shapes, text, charts and tables (`scripts/svg_to_pptx.py`) — not full-page images and not template filling.
- Trusted content layer: factual materials (policy names, figures, cases) are retrieved via dknowc Trusted Search (`scripts/trusted_search.py`) from a corpus of authoritative documents; every claim is traceable.
- Two confirmation gates (search plan, structure plan) plus SVG quality checks (`scripts/svg_quality_checker.py`) before export.
- Dual provenance reports: an outline-stage report before the structure gate and a final-stage report at delivery (`scripts/render_trace_html.py`), both clickable and generated from the same answer file; deliverables are a three-piece set (`.pptx` + outline report + final report).
- Style presets (party/government-concise default, data charts, business report, formal elegant, training courseware) and multi-canvas support (16:9, 4:3, RED 3:4, 1:1, story 9:16, A4).
- API Key is injected only through the environment variable `DKNOWC_API_KEY`; the Skill does not include a local `config.ini` and does not contain any real API Key.
- v1.0.1 optimized the onboarding flow for enabling trusted retrieval: value-first, timing-deferred phone-verification guidance with a graceful fallback (material-only mode with "数据待核验" placeholders when the user declines).
- v1.0.2 renamed the product to 深知可信PPT (technical identifiers unchanged), unified report naming to 可信溯源核验报告, and introduced the outline-stage report for the three-piece delivery; `render_trace_html.py` regression fixes cover Markdown table parsing, wide-table layout, marker-to-source mapping and marker position fallback.
- v1.0.3 refines external wording: the body and permission notes no longer name the upstream open-source project; third-party provenance and the MIT license are consolidated into THIRD_PARTY_NOTICES.md (retained for compliance).

Users can manage MaaS usage at https://platform.dknowc.cn/.

## 推荐 GitHub Topics

```text
agent-skills
skills-sh
ppt
powerpoint
presentation
office-automation
dknowc
svg
trusted-content
```
