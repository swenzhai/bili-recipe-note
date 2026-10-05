# 菜品特写选帧实测

2026-10-04，将原 `~/work/video-frame-choose` 工作目录移入 `external/video-frame-choose`，登记为 Git submodule，固定提交 `66b42b509e15ccc775172f7cb499428c08d945cf`。保留原独立环境、模型链接和验证产物，修复环境文本中的旧绝对路径。子模块源码未修改。

选帧子模块的远程来源配置为 `git@github.com:swenzhai/video-frame-choose.git`；在具备 SSH 访问权限的机器上，从主工程根目录执行 `git submodule sync -- external/video-frame-choose` 和 `git submodule update --init -- external/video-frame-choose` 初始化，详见主 README。本机 `data/sources/git/video-frame-choose.git` bare 仓库继续保留作为备份；此前在临时空仓库完成的初始化验证使用的是该本地备份。

## 素材和执行

- 视频：[王刚《番茄炒鸡蛋》](https://www.bilibili.com/video/BV13p411d7oQ)，来自已有菜谱的原视频链接。
- 本地文件：`data/experiments/frame-selection/tomato-eggs-BV13p411d7oQ/cover-video.mp4`。
- 实测时长 153.523 秒，1920×1080，25 fps，AV1，23,519,476 字节。使用本工程下载器获取单条视频，实验文件独立保存。
- 模型：本地正式 Qwen2.5-VL-3B-Instruct，CPU float32、16 线程；视觉推理离线执行。

从主工程执行的参数如下。输出目录若已有结果，应换新名字：

```bash
.venv/bin/python -m bili_recipe_notes.frame_selection \
  --input data/experiments/frame-selection/tomato-eggs-BV13p411d7oQ/cover-video.mp4 \
  --output data/experiments/frame-selection/tomato-eggs-BV13p411d7oQ/selection-01 \
  --requirement "找到一张制作完成、盛在盘中的番茄炒鸡蛋特写，菜品主体清晰突出，尽量没有手和锅铲遮挡" \
  --coarse-limit 24 --fine-limit 8 --top-k 3
```

## 结果

| 检查项 | 实测结果 |
| --- | --- |
| 工具状态 / 退出码 | `ok` / `0`，`fallback=false` |
| 候选 | 24 张粗筛 + 8 张精筛，3 张匹配 |
| 所选相对时间 | **126.0 秒（02:06）** |
| 解码帧序号 | 3150，从 0 开始 |
| PTS / time base | `2016704` / `1/16000` |
| 原始流时间 | 126.044 秒；首帧并非 0 秒，因此与相对时间不同 |
| 导出 | 原分辨率 1920×1080 RGB PNG |
| 像素验证 | 独立重解码所选帧，与 PNG 像素逐点完全一致 |
| 输入保护 | 视频 SHA-256 和修改时间保持一致 |
| 总耗时 | 728.36 秒，约 12 分 8 秒，包含进程启动、模型加载及导出 |

目视确认：画面主体为装盘后的番茄炒鸡蛋，能看到番茄、鸡蛋与葱花，没有手或锅铲遮挡。画面略柔，保留原视频底部字幕和左上水印，不能当作无文字的干净封面。模型确实排除了备菜、锅中翻炒、人物和片尾文字画面；本次结果验证了成品语义选择及原帧导出，不保证其他视频效果，也不保证有限采样选中整段视频的最优一帧。

结果位于上述实验目录的 `selection-01/`：

- `best_frame.png`：可查看的截图。
- `result.json`：所有候选判断、所选帧信息、参数和理由。
- `verification.json`：像素、时间戳、源文件和目视验证结果。
- `command.json`、`stdout.json`、`stderr.log`、`process.json`：实际命令、输出、进度及退出状态。

实验目录下 `verify_selection.py` 可用子模块解释器重跑自动校验；重跑后其中人工复核字段会重置为待检查。

主工程选帧入口与原截图相关测试 **24 项通过**；搬迁后的子模块测试 **29 项通过**；`compileall` 和 Git 差异检查通过。验证未将实验截图应用到原菜谱封面或步骤图片。
