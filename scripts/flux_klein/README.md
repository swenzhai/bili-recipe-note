# FLUX.2 Klein 4B 服务

服务器：`10.21.176.48`；目录：`/data04/swenz/flux-klein`。

采用 [BFL 官方模型卡](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B#using-with-diffusers-) 的 `Flux2KleinPipeline`、BF16、CPU offload、4 步和 guidance 1.0。支持文生图、参考图编辑。权重使用 ModelScope 同名仓库下载，保存下载清单并验证 SHA256。运行环境为 Python 3.12、PyTorch 2.9.0+cu128、Diffusers 0.37.1、Transformers 4.57.5；复用已有 Python 基础环境的只读依赖，新增包安装到服务独立 venv，不升级共享驱动。

入口：`http://10.21.176.48:8766`。`GET /health` 查看服务状态（不代表模型已经通过推理验收）。`POST /generate` 使用 `Authorization: Bearer <token>`，令牌存于服务器 `api-token`（权限 600），不写入仓库。请求示例：

```json
{"prompts":["Professional food photography of tomato and egg stir fry, white plate, natural light, no text"],"width":1024,"height":1024,"seed":42,"reference_images":[]}
```

`reference_images` 接受最多 3 张 base64 编码图片；`prompts` 最多 4 条，同一批次共用模型。返回 `job_id`、每张图片文件名、耗时与实际 GPU。`GET /images/{job_id}/{index}` 下载图片，亦需认证。输出保存在 `outputs/<job_id>/`，PNG 元数据标明 AI 生成。

复用 OCR `run_job.reserve_gpu` 的实时资源检查和 UUID 文件锁，要求至少 20 GiB 空闲，无空闲卡返回错误，不抢占其他用户 GPU。不固定 GPU；批次完成或超时退出子进程，释放模型。当前同步 API 同时仅接受一批请求，忙时返回 409；暂无持久业务队列，尚未接入菜谱软件 UI。

管理命令（swenz 用户）：

```sh
systemctl --user status flux-klein
systemctl --user restart flux-klein
systemctl --user stop flux-klein
tail -f /data04/swenz/flux-klein/logs/service.log
```

用户已启用 linger。`smoke_test.py` 通过真实 HTTP API 验证文生图和参考图编辑，保存 `smoke-results.json`。不要将仅服务健康响应当成生成验收成功。大量图片生成前应扩展持久队列和容量管理。

## 本次验收（2026-11-25）

真实 HTTP API：文生图和单参考图编辑均成功（1024×1024，4 步，A30 动态选卡）。文生图 HTTP 87.07 秒，模型加载与推理 79.20 秒；编辑 HTTP 39.77 秒，模型加载与推理 31.59 秒。峰值 PyTorch 分配显存分别 7996 / 9452 MiB，不含全部 CUDA 开销。单次冷启动数据不可外推为稳定吞吐。详情见 `acceptance-results.json`。

目视检查：生成了番茄和鸡蛋的白盘照片；编辑保留主体布局并换成蓝盘、浅色木桌面。不过番茄呈生切片外观，仍需菜谱做法一致性审核，不能因图片美观就自动确认封面。任务结束后 A30 显存恢复到 1 MiB。服务存储约 15 GiB，原菜谱未修改。

连续 HTTP 请求现在复用持久 worker，保留官方 CPU offload；结果记录 worker_pid、session_request 和 load_seconds。队列暂停/结束调用认证的 POST /release；空闲 120 秒或父进程退出自动释放 GPU 锁与显存。
