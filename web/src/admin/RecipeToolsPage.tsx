import { useEffect, useState } from 'react';
import { api, errorMessage, post } from './api';
import type { PageProps, RecipeDocument } from './types';
import './admin-tasks.css';

type Segment = { start: number; end?: number; text: string; [key: string]: unknown };
type LegacyItem = { id: string; section: string; index: number; decision: string; original: Record<string, unknown>; value: Record<string, unknown>; comment: string };
type ToolDetail = { id: string; revision: string; recipe: RecipeDocument; transcript: Segment[]; analysis: string; legacy_current: boolean; legacy_error?: string; legacy_review?: { status: string; items: LegacyItem[] } };
type Candidate = { id: string; url: string; timestamp: number | null; score: number; step_index: number; revision: string };
type Operation = { id: string; status: string; error?: string; result?: { message?: string; candidates?: Candidate[]; markdown?: string; backup?: string; warnings?: string[]; url?: string; name?: string } };
const fieldNames: Record<string, string> = { name: '名称', amount: '用量', note: '备注', evidence: '字幕依据', source_time: '来源时间（秒）', confidence: '提取置信度', title: '步骤标题', action: '具体做法', start_time: '开始时间（秒）', end_time: '结束时间（秒）', heat: '火候', duration: '时长', tips: '提示' };

export default function RecipeToolsPage({ navigate }: PageProps) {
  const id = decodeURIComponent(window.location.pathname.split('/')[3] || '');
  const [detail, setDetail] = useState<ToolDetail | null>(null);
  const [tab, setTab] = useState(new URLSearchParams(window.location.search).get('tab') || 'note');
  const [exportFormat, setExportFormat] = useState('markdown');
  const [confirmed, setConfirmed] = useState(false);
  const [transcript, setTranscript] = useState<Segment[]>([]);
  const [editTranscript, setEditTranscript] = useState(false);
  const [analysisRequest, setAnalysisRequest] = useState('提取视频中提到的通用烹饪技巧');
  const [step, setStep] = useState(1);
  const [timestamp, setTimestamp] = useState(0);
  const [upload, setUpload] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [itemIndex, setItemIndex] = useState(0);
  const [editedValue, setEditedValue] = useState<Record<string, unknown>>({});
  const [comment, setComment] = useState('');
  const [operationId, setOperationId] = useState(new URLSearchParams(window.location.search).get('operation') || '');
  const [operation, setOperation] = useState<Operation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [refresh, setRefresh] = useState(0);
  const working = busy || Boolean(operationId && (!operation || ['queued', 'running'].includes(operation.status)));
  const item = detail?.legacy_review?.items[itemIndex];
  const currentStep = detail?.recipe.steps?.[step - 1];

  useEffect(() => {
    const controller = new AbortController();
    void api<ToolDetail>(`/recipes/${encodeURIComponent(id)}/tools`, { signal: controller.signal }).then(value => {
      setDetail(value); setTranscript(value.transcript); setEditTranscript(false); setConfirmed(false);
    }).catch(error => { if (error.name !== 'AbortError') setError(errorMessage(error)); });
    return () => controller.abort();
  }, [id, refresh]);
  useEffect(() => { setEditedValue({ ...(item?.value || {}) }); setComment(item?.comment || ''); }, [item]);
  useEffect(() => { setTimestamp(Number(currentStep?.start_time || 0)); setUpload(null); }, [step, currentStep?.start_time]);
  useEffect(() => {
    if (!operationId) return;
    let cancelled = false;
    let pending = false;
    const controller = new AbortController();
    let timer: number;
    const poll = async () => {
      if (pending) return;
      pending = true;
      try {
        const value = await api<Operation>(`/tasks/settings/${encodeURIComponent(operationId)}`, { signal: controller.signal });
        if (cancelled) return;
        setOperation(value);
        if (value.status === 'completed') {
          window.clearInterval(timer);
          setNotice(value.result?.message || '操作已完成，已刷新当前菜谱。');
          if (value.result?.candidates) { setCandidates(value.result.candidates); setTab('images'); }
          setRefresh(value => value + 1);
        } else if (['failed', 'interrupted', 'completed_with_errors'].includes(value.status)) {
          window.clearInterval(timer); setError(value.error || '操作未完成，可以到任务中心查看错误并重试。');
        }
      } catch (error) { if (!cancelled) setError(errorMessage(error)); }
      finally { pending = false; }
    };
    timer = window.setInterval(() => void poll(), 2500); void poll();
    return () => { cancelled = true; controller.abort(); window.clearInterval(timer); };
  }, [operationId]);

  async function start(action: string, extra: Record<string, unknown> = {}) {
    if (!detail) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await post<Operation>(`/recipes/${encodeURIComponent(id)}/tools/${action}`, {
        revision: detail.revision, confirmed: action === 'review-item' || confirmed, step_index: step, timestamp, ...extra,
      });
      setOperationId(result.id); setOperation(result);
      navigate(`/recipes/${encodeURIComponent(id)}/tools?operation=${encodeURIComponent(result.id)}&tab=${encodeURIComponent(tab)}`);
    } catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  async function uploadImage(file?: File) {
    if (!file) return;
    setError('');
    try {
      if (file.size > 10 * 1024 * 1024) throw new Error('步骤图片不能超过 10 MB');
      const reader = new FileReader();
      const value = await new Promise<string>((resolve, reject) => { reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = reject; reader.readAsDataURL(file); });
      setUpload(value);
    } catch (error) { setError(errorMessage(error)); }
  }
  function updateSegment(index: number, values: Partial<Segment>) { setTranscript(items => items.map((item, i) => i === index ? { ...item, ...values } : item)); }
  const pending = detail?.legacy_review?.items.filter(item => item.decision === 'pending').length || 0;

  return <div className="adm-page"><header className="adm-page-head"><div><p className="adm-eyebrow">让每次整理，都留有依据</p><h1>菜谱修订工具</h1><p className="adm-muted">{detail?.recipe.title || '正在读取菜谱'} · 整理笔记、修订字幕和替换步骤图片</p></div><button className="adm-btn secondary" onClick={() => navigate(`/recipes/${encodeURIComponent(id)}`)}>返回菜谱</button></header>
    {error && <div className="adm-error" role="alert">{error}<button className="adm-btn small" onClick={() => { setError(''); setRefresh(value => value + 1); }}>刷新内容</button></div>}
    {notice && <div className="adm-notice" role="status">{notice}</div>}
    {operationId && <div className="adm-panel"><div className="adm-task-card-head"><strong>{working ? '操作正在后台进行…' : operation?.status === 'completed' ? '最近操作已完成' : '最近操作需要处理'}</strong><button className="adm-btn small secondary" onClick={() => navigate(`/tasks?kind=settings&id=${encodeURIComponent(operationId)}`)}>查看任务详情</button></div>{working && <p className="adm-muted">可以离开页面，任务会继续；结果应用前会再次检查是否有其他修改。</p>}{operation?.result?.backup && <p className="adm-muted">本次操作已保留上一版备份。</p>}{operation?.result?.warnings?.map((warning, index) => <p className="adm-notice" key={index}>{warning}</p>)}{operation?.result?.url && <a className="adm-btn primary" href={operation.result.url} download>下载 {operation.result.name || '菜谱文件'}</a>}</div>}
    {detail && <>{tab !== 'export' && <div className="adm-panel"><label className="adm-task-check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />我确认将本次修订写入生成版文件，并在修改前保留备份</label><p className="adm-muted">这些操作用于修改菜谱或附属文件。若只需记录来源核对意见，请进入<button className="adm-btn small" onClick={() => navigate(`/review?id=${encodeURIComponent(id)}`)}>独立审核工作台</button>。</p></div>}
      <div className="adm-tabs">{[['note', '笔记与二次分析'], ['transcript', '字幕修订'], ['images', '步骤图片'], ['legacy', '生成版逐项审核'], ['export', '导出菜谱']].map(([value, label]) => <button key={value} className={tab === value ? 'active' : ''} onClick={() => setTab(value)}>{label}</button>)}</div>
      {tab === 'export' && <section className="adm-panel"><h2>把这道菜带走</h2><p className="adm-muted">导出当前菜谱的独立副本，方便打印、分享或离线保存。此操作只读取现有资料。</p><label className="adm-field">导出格式<select value={exportFormat} onChange={event => setExportFormat(event.target.value)}><option value="markdown">Markdown 笔记（.md）</option><option value="obsidian">Obsidian Markdown（含笔记属性）</option><option value="pdf">PDF 图文文档（.pdf）</option><option value="docx">Word 图文文档（.docx）</option><option value="zip">菜谱资料包（.zip，含图片）</option></select></label><p className="adm-muted">{['markdown', 'obsidian'].includes(exportFormat) ? 'Markdown 保留图片的相对路径；需要一起带走图片时请选择 ZIP 资料包。' : exportFormat === 'zip' ? '资料包包含笔记、结构化菜谱、字幕、步骤图片与已有实践记录。' : '按笔记正文排版，包含本地步骤图片。打印前可先检查导出内容。'}</p><button className="adm-btn primary" disabled={working} onClick={() => void start('export', { format: exportFormat })}>生成可下载文件</button></section>}
      {tab === 'note' && <div className="adm-import-grid"><section className="adm-panel"><h2>整理现有笔记</h2><p className="adm-muted">使用设置中的文本模型整理措辞与结构。重写从当前菜谱生成正文，无需重新下载视频。</p><div className="adm-actions"><button className="adm-btn primary" disabled={working || !confirmed} onClick={() => void start('optimize')}>优化当前笔记</button><button className="adm-btn secondary" disabled={working || !confirmed} onClick={() => void start('rewrite')}>根据菜谱重写笔记</button></div></section><section className="adm-panel"><h2>从现有资料继续分析</h2><label className="adm-field">想了解什么<textarea rows={4} value={analysisRequest} onChange={event => setAnalysisRequest(event.target.value)} /></label><p className="adm-muted">结果单独保存为补充分析；资料不足的内容应保留说明。</p><button className="adm-btn primary" disabled={working || !confirmed || !analysisRequest.trim()} onClick={() => void start('analysis', { request: analysisRequest })}>开始二次分析</button></section>{detail.analysis && <section className="adm-panel"><h2>已有分析</h2><div style={{ whiteSpace: 'pre-wrap' }}>{detail.analysis}</div></section>}</div>}
      {tab === 'transcript' && <section className="adm-panel"><h2>核对字幕，重新整理做法</h2><p className="adm-muted">此功能使用字幕规则重新提取用料和步骤，将替换现有菜谱及笔记。内容变化后需重新核对 AI 审核。</p><label className="adm-task-check"><input type="checkbox" checked={editTranscript} onChange={event => setEditTranscript(event.target.checked)} />同时修订下方字幕文本与起止时间</label><div style={{ maxHeight: 520, overflow: 'auto' }}>{transcript.map((segment, index) => <div className="adm-panel" key={index}><div className="adm-form-grid"><label className="adm-field">开始时间（秒）<input type="number" min="0" step="0.1" readOnly={!editTranscript} value={segment.start} onChange={event => updateSegment(index, { start: Number(event.target.value) })} /></label><label className="adm-field">结束时间（秒）<input type="number" min={segment.start} step="0.1" readOnly={!editTranscript} value={segment.end ?? segment.start} onChange={event => updateSegment(index, { end: Number(event.target.value) })} /></label></div><label className="adm-field">字幕原文<textarea rows={2} readOnly={!editTranscript} value={segment.text} onChange={event => updateSegment(index, { text: event.target.value })} /></label></div>)}</div><button className="adm-btn primary" disabled={working || !confirmed || !transcript.length} onClick={() => void start('reextract', editTranscript ? { transcript: transcript.map(segment => ({ ...segment, end: segment.end ?? segment.start })) } : {})}>{editTranscript ? '保存字幕并重新提取菜谱' : '根据已有字幕重新提取'}</button></section>}
      {tab === 'images' && <section className="adm-panel"><h2>为关键步骤留下画面</h2><div className="adm-form-grid"><label className="adm-field">选择步骤<select value={step} onChange={event => setStep(Number(event.target.value))}>{detail.recipe.steps?.map((value, index) => <option key={index} value={index + 1}>{index + 1}. {value.title || value.action}</option>)}</select></label><label className="adm-field">截图时间（秒）<input type="number" min="0" max="86400" step="0.1" value={timestamp} onChange={event => setTimestamp(Number(event.target.value))} /></label></div><p>{currentStep?.action}</p>
        <div className="adm-actions"><button className="adm-btn secondary" disabled={working || !currentStep} onClick={() => void start('candidates')}>自动查找候选图</button><button className="adm-btn primary" disabled={working || !confirmed || !currentStep} onClick={() => void start('recapture')}>按此时间重新截图</button><button className="adm-btn secondary" disabled={working || !confirmed || !currentStep} onClick={() => void start('clear-image')}>此步骤不配图</button></div><p className="adm-muted">自动查找只生成候选。采用后才更新步骤图片；必要时会在后台下载源视频。</p>
        <div className="adm-form-grid">{candidates.filter(value => value.step_index === step).map(candidate => <div className="adm-panel" key={candidate.id}><img src={candidate.url} alt={`第 ${step} 步候选画面`} style={{ width: '100%', borderRadius: 12 }} /><p>{candidate.timestamp == null ? '上传画面' : `${candidate.timestamp.toFixed(1)} 秒`} · 清晰度 {Math.round(candidate.score * 100)}%</p><button className="adm-btn secondary" disabled={working || !confirmed || candidate.revision !== detail.revision} onClick={() => void start('choose-image', { candidate: candidate.id })}>{candidate.revision === detail.revision ? '采用这张图片' : '候选已过期，请重新查找'}</button></div>)}</div>
        <label className="adm-import-file">上传真实步骤照片<input type="file" accept="image/jpeg,image/png,image/webp" onChange={event => void uploadImage(event.target.files?.[0])} /></label>{upload && <img src={`data:image/jpeg;base64,${upload}`} alt="待采用的步骤照片" style={{ width: 240, maxWidth: '100%', borderRadius: 12 }} />}<button className="adm-btn primary" disabled={working || !confirmed || !upload || !currentStep} onClick={() => void start('upload-image', { content: upload })}>采用上传图片</button>
      </section>}
      {tab === 'legacy' && <section className="adm-panel"><h2>生成版逐项审核</h2><p className="adm-muted">用于修订生成菜谱中的用料与步骤。保存逐项意见只更新这份审核记录；全部处理后，点击“应用”才改写生成版菜谱。</p>{detail.legacy_error && <p className="adm-error">旧审核记录无法读取：{detail.legacy_error}</p>}
        {detail.legacy_review && !detail.legacy_current && <p className="adm-notice">当前菜谱与这份审核的原始内容不同。请重新建立审核，旧记录会保留备份。</p>}
        <button className="adm-btn secondary" disabled={working || !confirmed} onClick={() => void start('create-review')}>{detail.legacy_review ? '根据当前菜谱重新建立审核' : '建立逐项审核'}</button>
        {detail.legacy_review && <><p>已处理 {detail.legacy_review.items.length - pending} / {detail.legacy_review.items.length} 项</p><label className="adm-field">审核项<select value={itemIndex} onChange={event => setItemIndex(Number(event.target.value))}>{detail.legacy_review.items.map((value, index) => <option key={value.id} value={index}>{({ ingredients: '食材', seasonings: '调料', steps: '步骤' } as Record<string, string>)[value.section]} {value.index + 1} · {({ pending: '待处理', accepted: '已采用', edited: '已修订', skipped: '已移除' } as Record<string, string>)[value.decision]}</option>)}</select></label>
          {item && <><div className="adm-form-grid">{Object.entries(editedValue).filter(([key]) => key in fieldNames).map(([key, value]) => <label key={key} className="adm-field">{fieldNames[key]}<input type={['start_time', 'end_time', 'source_time', 'confidence'].includes(key) ? 'number' : 'text'} step="any" value={value == null ? '' : String(value)} onChange={event => setEditedValue(previous => ({ ...previous, [key]: ['start_time', 'end_time', 'source_time', 'confidence'].includes(key) ? event.target.value === '' ? null : Number(event.target.value) : event.target.value }))} /></label>)}</div><label className="adm-field">核对备注<textarea rows={2} value={comment} onChange={event => setComment(event.target.value)} /></label><div className="adm-actions">{[['accepted', '采用原项'], ['edited', '保存修改后采用'], ['skipped', '从生成版移除此项']].map(([decision, label]) => <button className="adm-btn secondary" key={decision} disabled={working || !detail.legacy_current} onClick={() => void start('review-item', { item_id: item.id, decision, comment, ...(decision === 'edited' ? { value: editedValue } : {}) })}>{label}</button>)}</div></>}
          <div className="adm-actions" style={{ marginTop: 24 }}><button className="adm-btn primary" disabled={working || !confirmed || pending > 0 || !detail.legacy_current} onClick={() => void start('apply-review')}>应用审核结果并更新生成版</button><span className="adm-muted">{pending ? `还有 ${pending} 项待处理` : '所有条目已处理'}</span></div>
        </>}
      </section>}
    </>}
  </div>;
}
