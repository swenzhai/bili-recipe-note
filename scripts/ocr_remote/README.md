# 远端 OCR 封面批量选择

本目录保存部署到 OCR 服务器的 worker 和结果归档工具。服务器入口仍为
`/data04/swenz/ocr-on-demand/ocr`，不得直接运行 worker 加载模型；入口负责动态选卡、持锁、整批超时与退出后的进程清理。

## 必选最佳候选

```sh
ocr dish --batch /服务器/input.json --rank-batch --output /新的/summary.json --timeout 18000
```

每条输入包含 `image`（带 ID 编号的候选拼图）、`candidate_count`、`target`、
`group` 和独立 `output`。同一道菜所有截图一起比较，模型逐项评分；完全匹配优先，
无完全匹配时仍选最高分。降级标志 `fallback=true` 保留真实判定，不将过程图伪装成成品。
相同评分按 ID 稳定选择。输入与结果里的文本均作为数据处理。

模型只加载一次，整个批次共享，结束或超时后释放显存。无空闲显卡返回 75，
不自动排队或停止其他用户进程。JSON 格式错误单条重试一次，仍失败记录真实错误，
不能把技术失败当成成功选择；批次继续。没有任何可读取图片属于缺少输入，不能凭空选图。

每道菜保存各候选评分、最佳 ID、原因和降级标志。`summary.json` 原子更新；
只有进程退出码为 0 且最终状态 complete 才表示正常完成，completed_with_errors
需检查具体失败条目。已有输出不可覆盖。

`prepare_pilot.py` 在上一批最后一道之后，按相同排序选 100 道可展示、已上架、
未人工确认封面的菜，只读取现有步骤截图，生成编号拼图及原文件哈希。
`collect_pilot.py` 下载结果后复制所选原图到独立目录，输出 review.html，并验证哈希。
这些独立结果尚不属于管理页自动选图接口，不会更新 recipe.json 或菜单封面。

```sh
PYTHONPATH=. .venv/bin/python scripts/ocr_remote/prepare_pilot.py \
  --output data/recipes/.frame-selection/新批次名 \
  --previous data/recipes/.frame-selection/上一批次/state.json
PYTHONPATH=. .venv/bin/python scripts/ocr_remote/collect_pilot.py \
  data/recipes/.frame-selection/新批次名
python -m pytest -q tests/test_ocr_remote_ranking.py
```

服务器更新前需备份原 worker。部署 `judge_dish_once.py` 和同目录 `ranking.py`，
现有单图与 `--batch` 调用保持原行为；显式 `--rank-batch` 使用新必选规则。

## 指定批量数量与持久运行

准备下一批时可传 `--limit 500`，默认仍为100。有图但不匹配时必选最高分；
没有可读截图的条目记录在 skipped 中，顺延补足指定数量。

`run_pilot.py OUTPUT --control-path SSH_CONTROL_PATH --timeout 64800` 上传输入并调用正式入口，
每分钟同步 summary 与浏览页，结束保存 execution.json、REPORT.md 和 GPU 状态。
需要已认证的 SSH 连接，不保存密码。可由独立进程运行，关闭对话不影响处理。
最终检查 execution.json 的状态和退出码；运行中不得当作已完成。

## 正式软件接口：完整视频选帧

后续任务应调用 `frame_selection_runner.start_frame_selection_batch` / `resume_frame_selection_batch`，
不再用上述截图拼图脚本代替产品选帧。指定 `backend="remote"`、`remote_host="swenz@10.21.176.48"`、
`remote_control_path`（已认证SSH控制连接）和 `allow_fallback=True`。

正式路径：后台任务 → 原视频下载 → 远端原版 `video_frame_choose.pipeline.select()` →
粗筛/邻近精筛或全片补采样 → 原版 `LocalJudge.judge_frame()` 的独立观察、完成状态、
身份及歧义复核 → 原分辨率/真实PTS导出 → 本地结果校验及候选索引。
远端适配只替换 `_generate` 与模型加载，不替换采样、提示词或判断流程。
默认使用24帧粗筛、8帧精筛、5fps精筛频率；每道可能涉及多轮模型推理，不能套用拼图任务耗时。

部署 `frame_session.py` 与最新版 `judge_dish_once.py` 到服务器 scripts，
并将主仓库固定版本的 `external/video-frame-choose/video_frame_choose` 原样复制到同目录。
视频依赖 PyAV 可装到 `scripts/frame-deps`，避免改变共享模型环境。
通过 `ocr dish --frame-session SESSION --output SESSION/unused.json` 开启模型会话；
仍由原OCR入口选择空闲GPU、持锁、超时和释放模型。客户端以原子请求文件提交视频任务，
远端进程独立于SSH连接；本批worker结束关闭模型，无任务15分钟也自动退出。
每道结果含远端原始元数据、本地映射结果和会话位置。原始素材不改写。

降级结果保留原始 `fallback` 标志和 reject 判断，在软件候选索引中标为 `needs_review`，
原因明确写明降级，已有审核工作台可以展示图片供人工确认，不自动替换封面。
队列及进度使用正式 `.frame-selection/tasks/frame-*.json`。界面尚未提供远端配置表单，
远端参数当前通过Python正式服务接口传入。

### 加速验证开关

正式服务接口可设置 `remote_acceleration=True`，仅为本次OCR进程添加隔离依赖路径
`/data04/swenz/ocr-on-demand/jobs/accel-validation-20261110/deps`，不修改基础模型环境。
其中 FLA/fla-core 0.6.0、einops 0.8.2、causal-conv1d 1.7.0；卷积扩展需强制本机源码编译，
上游自动获取的wheel要求GLIBC_2.32，不适用于当前服务器。使用CUDA12.4与MAX_JOBS=4完成本机构建。
测试会话在 kernels.json 保存实际绑定的实现；必须以真实推理耗时与质量验证决定是否启用。

### vLLM 隔离验证（尚未通过真实推理验收）

`remote_engine="vllm"` 选择隔离的 `envs/dish-vllm-validation` 环境，
通过原 OCR 入口显式申请 27B 档，继续持有同一 GPU 锁。它不能与
`remote_acceleration=True` 叠加。`frame_vllm.py` 仅替换生成调用，仍复用
原版选帧与判断；温度0、关闭思考、输出上限512 token。每次生成记录输入/输出
 token 数及整体生成耗时（不是纯 decode tokens/s）到会话的 `generation-metrics.jsonl`。

当前候选为 vLLM 0.23.0 CUDA12.9 manylinux_2_28，避免最新版本的 CUDA13
及旧系统库不兼容。NF4 权重加载、驱动兼容和真实速度均需服务器验收后才能推广。
原队列在验收期间保持暂停，不代表已切换生产引擎。

2026-11-10 单菜真实验收完成：枣馍32帧、65次生成，选帧315.13秒，
原版1012.23秒，FLA版869.13秒；单样本流程加速3.21倍，非纯decode吞吐对比。
输出1920×1080与原视频PTS逐像素一致，OCR退出0、A100恢复1MiB。
新结果选择343.833秒（原版335.625秒），存在明显手部遮挡，尚不能宣称质量等价。
完整记录位于 `.frame-selection/acceleration-validation/vllm-results.json`。

vLLM 0.23的NVML查询不能直接处理CUDA_VISIBLE_DEVICES中的UUID，隔离环境需运行
`patch_vllm_uuid.py`，保留原文件备份；补丁仅将UUID映射到NVML索引，不改显卡可见范围。
启动显式设置CUDA_HOME=/usr/local/cuda-12.4，VLLM_USE_FLASHINFER_SAMPLER=0，
使用vLLM自带采样器，避免FlashInfer采样扩展错误调用系统CUDA10.1。
FlashInfer缓存指定到/data04，MAX_JOBS=4。所有真实推理仍由OCR监督器持锁并清理。

完整视频会话总时限为7天，以支持491道的连续批次；每道仍限6小时，无请求15分钟退出，批次结束立即关闭，不等待总时限。

单条视频下载或推理失败时记录原因并继续后续条目，不再因连续五条失败暂停。
模型环境不可用（包括没有空闲GPU）仍停止。视频下载完成后才获取模型会话；
复用前检查退出标记，已退出的会话通过OCR正式入口重新选卡、加锁并加载。
