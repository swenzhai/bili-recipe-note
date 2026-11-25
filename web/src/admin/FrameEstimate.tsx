import { useState } from 'react';

// Fixed, versioned observations; never infer hardware from a CUDA device label.
export const frameBenchmarks = [
  { id: 'unknown', label: '当前设备（尚未匹配实测）', seconds: null, low: null, high: null, samples: 0, note: 'CPU / CUDA 不能唯一确定硬件、模型和推理引擎。请选择可比方案，或填写单菜实测。' },
  { id: 'a100-vllm', label: 'A100 40GB · 27B NF4 · vLLM', seconds: 303.73, low: 237.12, high: 373.71, samples: 44, note: '44 道端到端实测，含下载、传输和选帧；单菜 P10–P90 约 4–6.2 分钟。批次共享模型。' },
  { id: 'a100-fla', label: 'A100 40GB · 27B NF4 · Transformers 加速算子', seconds: 869.13, low: null, high: null, samples: 1, note: '仅枣馍单菜选帧实测，不含模型启动、下载传输；不能视为批量平均值。' },
  { id: 'a100-original', label: 'A100 40GB · 27B NF4 · Transformers 原部署', seconds: 1012.23, low: null, high: null, samples: 1, note: '仅枣馍单菜选帧实测，不含模型启动、下载传输；未安装加速算子。' },
  { id: 'a30', label: 'A30 24GB', seconds: null, low: null, high: null, samples: 0, note: '暂无完整视频实测。当前 vLLM 方案占约 32.6 GiB，不能直接套用到 24GB 显卡；更换模型或配置后需重新测量。' },
  { id: '3080ti', label: 'RTX 3080 Ti 12GB', seconds: null, low: null, high: null, samples: 0, note: '暂无完整视频实测，当前 27B 部署无法直接容纳。不能用显卡算力比例推算另一模型的耗时。' },
  { id: 'cpu', label: 'CPU', seconds: null, low: null, high: null, samples: 0, note: '暂无 CPU 完整视频实测；处理器、线程数、内存和模型差异很大。可先测一道，再填写下方耗时。' },
] as const;

export function estimateFrameTime(profileId: string, total: number | null, runLimit: number, coarse: number, fine: number, customMinutes: number | null = null) {
  const profile = frameBenchmarks.find(item => item.id === profileId) || frameBenchmarks[0];
  const validCount = (n: number) => Number.isSafeInteger(n) && n >= 0;
  if (!validCount(runLimit) || (total !== null && !validCount(total)) || !Number.isSafeInteger(coarse) || coarse < 2 || !Number.isSafeInteger(fine) || fine < 1) return null;
  const custom = customMinutes !== null;
  const seconds = custom ? customMinutes * 60 : profile.seconds;
  if (seconds === null || !Number.isFinite(seconds) || seconds <= 0) return null;
  const factor = (coarse + fine) / 32;
  const count = total === null ? (runLimit || null) : runLimit ? Math.min(runLimit, total) : total;
  return { perDish: seconds * factor, batch: count === null ? null : count * seconds * factor,
    total: total === null ? null : total * seconds * factor, count, scaled: factor !== 1, custom,
    low: !custom && profile.low !== null && count !== null ? count * profile.low * factor : null,
    high: !custom && profile.high !== null && count !== null ? count * profile.high * factor : null };
}

export function estimateDuration(seconds: number | null) {
  if (seconds === null) return '数量未知，暂无法计算';
  if (seconds === 0) return '无需执行';
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return `约 ${minutes} 分钟`;
  return `约 ${Math.floor(minutes / 60)} 小时${minutes % 60 ? ` ${minutes % 60} 分钟` : ''}`;
}

export default function FrameEstimate({ total, runLimit, coarse = 24, fine = 8 }: { total: number | null; runLimit: number; coarse?: number; fine?: number }) {
  const [profileId, setProfileId] = useState('unknown');
  const [custom, setCustom] = useState('');
  const profile = frameBenchmarks.find(item => item.id === profileId) || frameBenchmarks[0];
  const estimate = estimateFrameTime(profileId, total, runLimit, coarse, fine, custom.trim() ? Number(custom) : null);
  return <section className="adm-panel" aria-label="选图经验耗时预估">
    <h3>启动前 · 经验耗时预估</h3>
    <label className="adm-field">参考硬件与模型<select value={profileId} onChange={event => { setProfileId(event.target.value); setCustom(''); }}>{frameBenchmarks.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
    <p className="adm-muted">仅用于时间比较，不会切换任务的设备、模型或引擎。请与实际执行配置核对。</p>
    <p>{profile.note}</p>
    <label className="adm-field">自己的单菜实测（分钟，可选）<input type="number" min="0.01" step="any" value={custom} placeholder="相同设备、模型，24＋8 帧" onChange={event => setCustom(event.target.value)} /></label>
    {estimate ? <div aria-live="polite"><p><strong>本批{estimate.count !== null ? `最多 ${estimate.count} 道` : ''}：{estimateDuration(estimate.batch)}</strong></p><p>全列表：{estimateDuration(estimate.total)} · 单菜：{estimateDuration(estimate.perDish)}</p>
      {estimate.low !== null && <p>按样本快慢区间外推本批：{estimateDuration(estimate.low)} 至 {estimateDuration(estimate.high)}（不是完成时间保证）。</p>}
      {estimate.scaled && <p>当前 {coarse}＋{fine} 帧：按帧数线性外推，非该配置实测，多轮复核会影响耗时。</p>}
      {estimate.custom && <p>依据用户填写的单菜耗时外推，未经过软件验证。</p>}
    </div> : <p role="status">暂无可信耗时估计；可选择已有实测方案或填写有效单菜耗时。</p>}
    <p className="adm-muted">数据日期：2026-11-10。仅适用于完整视频选图，不适用于截图拼图。跳过有效结果、视频长度、网络和复核次数会改变实际数量及耗时；启动与排队另计。A100 vLLM 首次加载和预热曾约 5 分钟。</p>
  </section>;
}
