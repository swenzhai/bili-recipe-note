import { useEffect, useRef, useState } from 'react';
import { api, errorMessage, post, safeUrl, taskPath } from './api';
import type { PageProps, RecipeDetail, RecipeSummary, TaskSummary } from './types';
import './review.css';

type Evidence = { text: string; start?: number };
type ItemDecision = { verdict?: string; comment?: string; evidence?: Evidence[] };
type Review = { content_fingerprint?: string; classification?: string; ai_valid?: boolean; ai_invalid_reason?: string; manual_completed?: boolean; item_decisions?: Record<string, ItemDecision>; ai_review?: { reason?: string; confidence?: number; evidence?: Evidence[]; item_checks?: { id: string; verdict: string; reason: string }[] }; record?: { visibility_override?: string; classification_override?: string }; error?: string };
type Candidate = { id: string; url: string; label: string; timestamp?: number | null; candidate_revision?: string };
const classes = { recipe: '菜谱', technique: '烹饪技巧', non_recipe: '非菜谱', uncertain: '待确认' };
const sections = { ingredients: '食材', seasonings: '调料', steps: '步骤' } as const;

export function aiFrameCandidates(frame: RecipeDetail['frame_candidate']): Candidate[] {
  if (!frame || !['ok', 'needs_review'].includes(String(frame.status)) || !frame.url) return [];
  return [{ id: 'ai', url: String(frame.url), label: frame.status === 'needs_review' ? 'AI 候选 · 待确认' : 'AI 成品特写',
    timestamp: Number(frame.timestamp), candidate_revision: String(frame.candidate_revision) }];
}

function ItemCheck({ id, title, original, reason, decision, save, disabled }: { id: string; title: string; original: string; reason?: string; decision?: ItemDecision; save: (body: Record<string, unknown>) => Promise<void>; disabled: boolean }) {
  const [comment, setComment] = useState(decision?.comment || '');
  const [quote, setQuote] = useState(decision?.evidence?.map(item => item.text).join('\n') || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function confirm(verdict: string) {
    setBusy(true); setError('');
    try { await save({ action: 'item', item_id: id, verdict, comment, evidence: quote.trim() ? [{ text: quote.trim() }] : [] }); }
    catch (error) { setError(errorMessage(error)); } finally { setBusy(false); }
  }
  return <article className="adm-check-item"><div className="adm-check-title"><strong>{title}</strong><span className={`adm-badge ${decision?.verdict === 'supported' ? 'success' : 'warning'}`}>{decision?.verdict === 'supported' ? '已人工核实' : '待人工核对'}</span></div><p>{original}</p>{reason && <p className="adm-ai-reason">AI 意见：{reason}</p>}<details><summary>填写核对依据</summary><label className="adm-field">字幕原文引用<textarea value={quote} onChange={event => setQuote(event.target.value)} placeholder="从右侧字幕复制连续原文，保存时会核查引用" rows={2}/></label><label className="adm-field">人工核对说明<textarea value={comment} onChange={event => setComment(event.target.value)} placeholder="例如：已对照原视频核实用量；也可记录尚不明确的问题" rows={2}/></label></details>{error && <div className="adm-error" role="alert">{error}</div>}<div className="adm-actions"><button className="adm-btn small" disabled={disabled || busy || (!quote.trim() && !comment.trim())} onClick={() => void confirm('supported')}>确认有依据</button><button className="adm-btn small secondary" disabled={disabled || busy} onClick={() => void confirm('needs_review')}>标记待核实</button></div></article>;
}

function CropEditor({ candidate, busy, save }: { candidate: Candidate; busy: boolean; save: (crop: Record<string, number>) => void }) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [zoom, setZoom] = useState(1); const [x, setX] = useState(50); const [y, setY] = useState(50);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  useEffect(() => { setSize({ width: 0, height: 0 }); setZoom(1); setX(50); setY(50); }, [candidate.id]);
  const width = Math.floor(Math.min(size.width, size.height * 4 / 3) / zoom);
  const height = Math.floor(width * 3 / 4);
  const left = Math.round((size.width - width) * x / 100), top = Math.round((size.height - height) * y / 100);
  return <div className="adm-crop-editor"><div className="adm-crop-source" onPointerDown={event => { if (!size.width) return; event.currentTarget.setPointerCapture(event.pointerId); drag.current = { x: event.clientX, y: event.clientY, left: x, top: y }; }} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onPointerMove={event => {
    if (!drag.current) return; const rect = event.currentTarget.getBoundingClientRect();
    const dx = (event.clientX - drag.current.x) * size.width / rect.width;
    const dy = (event.clientY - drag.current.y) * size.height / rect.height;
    if (size.width > width) setX(Math.max(0, Math.min(100, drag.current.left + dx / (size.width - width) * 100)));
    if (size.height > height) setY(Math.max(0, Math.min(100, drag.current.top + dy / (size.height - height) * 100)));
  }}><img src={candidate.url} alt={`裁剪${candidate.label}`} draggable={false} onLoad={event => setSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}/>{size.width > 0 && <div className="adm-crop-box" style={{ left: `${left / size.width * 100}%`, top: `${top / size.height * 100}%`, width: `${width / size.width * 100}%`, height: `${height / size.height * 100}%` }}><span>拖动调整位置 · 4:3</span></div>}</div><div className="adm-form-grid"><label className="adm-field">裁剪缩放<input type="range" min="1" max="4" step="0.05" value={zoom} onChange={event => setZoom(Number(event.target.value))}/></label><label className="adm-field">水平位置<input type="range" min="0" max="100" value={x} onChange={event => setX(Number(event.target.value))}/></label><label className="adm-field">垂直位置<input type="range" min="0" max="100" value={y} onChange={event => setY(Number(event.target.value))}/></label></div><p className="adm-muted">{size.width ? `${size.width} × ${size.height} 原图 · 裁剪后 ${width} × ${height}` : '正在加载原图…'} · 确认后更新点餐封面。</p><button className="adm-btn primary" disabled={busy || !size.width || !height} onClick={() => save({ left, top, width, height })}>{busy ? '正在保存…' : '确认裁剪并设为封面'}</button></div>;
}

export default function ReviewPage({ navigate }: PageProps) {
  const params = new URLSearchParams(window.location.search), selectedId = params.get('id') || '';
  const [scope, setScope] = useState(params.get('scope') || 'pending'); const [search, setSearch] = useState(params.get('q') || ''); const [page, setPage] = useState(Math.max(1, Number(params.get('page')) || 1));
  const currentId = useRef(selectedId); currentId.current = selectedId;
  useEffect(() => { const restore = () => { const query = new URLSearchParams(window.location.search); setScope(query.get('scope') || 'pending'); setSearch(query.get('q') || ''); setPage(Math.max(1, Number(query.get('page')) || 1)); }; window.addEventListener('popstate', restore); return () => window.removeEventListener('popstate', restore); }, []);
  const [queue, setQueue] = useState<{ items: RecipeSummary[]; total: number }>({ items: [], total: 0 });
  const [data, setData] = useState<RecipeDetail | null>(null); const tab = params.get('tab') || 'content';
  const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [busy, setBusy] = useState(false);
  const [syncWarning, setSyncWarning] = useState('');
  const [loading, setLoading] = useState(false); const [revisionKey, setRevisionKey] = useState(0);
  const [classification, setClassification] = useState('uncertain'); const [comment, setComment] = useState(''); const [transcriptSearch, setTranscriptSearch] = useState('');
  const [extras, setExtras] = useState<Candidate[]>([]); const [candidate, setCandidate] = useState<Candidate | null>(null); const [timestamp, setTimestamp] = useState(0);
  const [history, setHistory] = useState<Record<string, unknown>[]>([]);
  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    query.set('scope', scope); if (search) query.set('q', search); else query.delete('q');
    query.set('page', String(page));
    window.history.replaceState({}, '', `/admin/review?${query}`);
  }, [scope, search, page]);
  useEffect(() => {
    const controller = new AbortController(); const timer = window.setTimeout(() => {
      const query = new URLSearchParams({ visibility: scope === 'hidden' ? 'hidden' : 'all', q: search, page: String(page), page_size: '20', ...(scope === 'pending' || scope === 'confirmed' ? (tab === 'cover' ? { cover_status: scope } : { review_status: scope }) : {}) });
      api<typeof queue>(`/recipes?${query}`, { signal: controller.signal }).then(setQueue).catch(error => { if (error.name !== 'AbortError') setError(errorMessage(error)); });
    }, 180); return () => { clearTimeout(timer); controller.abort(); };
  }, [scope, search, page, revisionKey, tab]);
  useEffect(() => {
    if (!selectedId) { setData(null); return; }
    const controller = new AbortController(); setLoading(true); setError(''); setSyncWarning(''); setData(null); setCandidate(null); setExtras([]); setHistory([]); setComment(''); setTranscriptSearch('');
    api<RecipeDetail>(`/recipes/${encodeURIComponent(selectedId)}`, { signal: controller.signal }).then(value => { setData(value); setClassification(String(value.review?.classification || 'uncertain')); }).catch(error => { if (error.name !== 'AbortError') setError(errorMessage(error)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [selectedId]);
  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    if (tab === 'cover') api<{ items: (Candidate & { source_kind?: string })[] }>(`/recipes/${encodeURIComponent(selectedId)}/cover-candidates`, { signal: controller.signal }).then(result => setExtras(result.items.map(item => ({ ...item, id: `candidate-${item.id}` })))) .catch(error => { if (error.name !== 'AbortError') setError(errorMessage(error)); });
    if (tab === 'history') api<{ items: Record<string, unknown>[] }>(`/recipes/${encodeURIComponent(selectedId)}/review-history`, { signal: controller.signal }).then(result => setHistory(result.items)).catch(error => { if (error.name !== 'AbortError') setError(errorMessage(error)); });
    return () => controller.abort();
  }, [selectedId, tab, revisionKey]);
  const review = (data?.review || {}) as Review;
  async function refresh() { if (!selectedId) return; const value = await api<RecipeDetail>(`/recipes/${encodeURIComponent(selectedId)}`); if (currentId.current === selectedId && value.id === selectedId) setData(value); setRevisionKey(key => key + 1); }
  async function saveReview(body: Record<string, unknown>) {
    const result = await post<{ sync_warning?: string }>('/recipes/' + encodeURIComponent(selectedId) + '/review', { ...body, content_fingerprint: review.content_fingerprint });
    let warning = result.sync_warning || '';
    try { await refresh(); } catch { warning += '核对意见已保存，页面刷新失败，请稍后刷新查看。'; }
    if (currentId.current === selectedId) { setNotice('核对意见已保存'); setSyncWarning(warning); }
  }
  async function saveCover(source: string, crop?: Record<string, number>, candidateRevision?: string) {
    if (!data) return;
    const result = await post<RecipeDetail>('/recipes/' + encodeURIComponent(selectedId) + '/cover', { revision: data.revision, source, crop, candidate_revision: candidateRevision });
    if (currentId.current === selectedId) {
      setData(result); setCandidate(null); setSyncWarning(result.sync_warning || '');
      setNotice(source === 'unavailable' ? '已标记暂无合适图片' : '封面已确认，点餐菜单会自动更新');
    }
    setRevisionKey(key => key + 1);
  }
  async function perform(action: () => Promise<void>) { setBusy(true); setError(''); setNotice(''); try { await action(); } catch (error) { setError(errorMessage(error)); } finally { setBusy(false); } }
  function open(id: string) { setNotice(''); const query = new URLSearchParams(window.location.search); query.set('id', id); query.set('tab', tab); navigate(`/review?${query}`); }
  async function start(kind: string) { const result = await post<TaskSummary>('/tasks', { kind, recipe_ids: [selectedId], limit: 1, only_pending: false, options: kind === 'frames' ? { skip_confirmed: false } : {} }); navigate(taskPath(result)); }
  async function capture(exact: boolean) { await post(`/recipes/${encodeURIComponent(selectedId)}/cover-candidates`, { timestamp: exact ? timestamp : null }); setRevisionKey(key => key + 1); setNotice('视频候选已准备好，请选择并裁剪'); }
  async function upload(file?: File) {
    if (!file) return; if (file.size > 10 * 1024 * 1024) { setError('图片不能超过 10 MB'); return; }
    const content = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = reject; reader.readAsDataURL(file); });
    await post(`/recipes/${encodeURIComponent(selectedId)}/cover-upload`, { content }); setRevisionKey(key => key + 1); setNotice('照片已上传，选择后可裁剪');
  }
  const candidates: Candidate[] = [...(data?.cover_url ? [{ id: 'cover', url: data.cover_url, label: '当前封面' }] : []), ...aiFrameCandidates(data?.frame_candidate), ...(data?.step_images || []).map(item => ({ id: `step-${item.index}`, url: item.url, label: `第 ${item.index + 1} 步图片` })), ...extras];
  const segments = (Array.isArray(data?.transcript) ? data.transcript : []) as { text: string; start?: number; start_time?: number }[];
  const source = safeUrl(data?.recipe.source_url);
  return <div className="adm-page"><div className="adm-page-head"><div><span className="adm-eyebrow">REVIEW STUDIO</span><h1>审核工作台</h1><p className="adm-muted">对照原视频与字幕，让菜谱与成品图都有据可依。</p></div><button className="adm-btn secondary" onClick={() => navigate('/tasks?create=review')}>批量 AI 审核</button></div>{error && <div className="adm-error" role="alert">{error}<button className="adm-btn small" onClick={() => void perform(refresh)}>刷新内容</button></div>}{notice && <div className="adm-notice" role="status">{notice}</div>}{syncWarning && <div className="adm-notice" role="status">{syncWarning}</div>}
    <div className="adm-review-layout"><aside className="adm-panel adm-review-queue"><label className="adm-field">查找菜谱<input value={search} onChange={event => { setSearch(event.target.value); setPage(1); }} placeholder="菜名、UP 主、食材"/></label><label className="adm-field">审核范围<select value={scope} onChange={event => { setScope(event.target.value); setPage(1); }}><option value="pending">待人工核对</option><option value="all">全部内容</option><option value="hidden">已隐藏</option><option value="confirmed">已人工确认</option></select></label><p className="adm-muted">共 {queue.total} 条</p><div className="adm-review-queue-items">{queue.items.map(item => <button key={item.id} className={`adm-review-row ${item.id === selectedId ? 'active' : ''}`} disabled={busy} onClick={() => open(item.id)}>{item.cover_url ? <img src={item.cover_url} alt="" loading="lazy"/> : <span className="adm-review-thumb">菜</span>}<span><strong>{item.title}</strong><small>{item.creator_name || item.category}{item.hidden ? ' · 已隐藏' : ''}</small></span></button>)}</div>{!queue.total && <p className="adm-muted">当前范围没有菜谱。</p>}<div className="adm-actions"><button className="adm-btn small" disabled={page === 1} onClick={() => setPage(page - 1)}>上一页</button><span>{page}</span><button className="adm-btn small" disabled={page * 20 >= queue.total} onClick={() => setPage(page + 1)}>下一页</button></div></aside>
    <section className="adm-review-main">{loading ? <div className="adm-empty" role="status">正在读取菜谱与审核记录…</div> : !data || data.id !== selectedId ? <div className="adm-empty"><h2>选一道菜，开始核对</h2><p>从左侧选择内容，查看来源依据或确认封面。</p>{queue.items[0] && <button className="adm-btn primary" onClick={() => open(queue.items[0].id)}>开始第一条</button>}</div> : <><div className="adm-panel adm-review-heading"><div><h2>{data.recipe.title}</h2><p className="adm-muted">{data.recipe.creator_name || data.recipe.uploader || '未知作者'} · {review.manual_completed ? '已人工确认' : '待人工核对'}</p></div><div className="adm-actions"><button className="adm-btn small" onClick={() => navigate(`/recipes/${encodeURIComponent(selectedId)}`)}>菜谱详情</button><button className="adm-btn small" onClick={() => navigate(`/recipes/${encodeURIComponent(selectedId)}/edit`)}>编辑菜谱</button>{source && <a className="adm-btn small" href={source} target="_blank" rel="noreferrer">原视频 ↗</a>}</div></div><div className="adm-tabs" role="tablist" aria-label="审核内容">{[['content', '内容与证据'], ['cover', '成品图'], ['history', '审核历史']].map(([key, title]) => <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? 'active' : ''} onClick={() => { const query = new URLSearchParams(window.location.search); query.set('id', selectedId); query.set('tab', key); navigate(`/review?${query}`); }}>{title}</button>)}</div>
    {tab === 'content' && <><div className="adm-panel"><div className="adm-actions"><h3>内容分类与展示</h3><button className="adm-btn small secondary" disabled={busy} onClick={() => void perform(() => start('review'))}>AI 重新审核</button></div>{!review.ai_valid && <p className="adm-notice">{review.ai_invalid_reason || '尚无有效 AI 审核，可先人工核对或启动 AI 审核。'}</p>}{review.ai_review && <p className="adm-ai-reason">AI 判断：{classes[(review.classification || 'uncertain') as keyof typeof classes]} · {Math.round((review.ai_review.confidence || 0) * 100)}%<br/>{review.ai_review.reason}</p>}<p className="adm-muted">{data.visibility?.reason}</p><div className="adm-form-grid"><label className="adm-field">人工分类<select value={classification} onChange={event => setClassification(event.target.value)}>{Object.entries(classes).map(([key, title]) => <option value={key} key={key}>{title}</option>)}</select></label><label className="adm-field">分类依据<input value={comment} onChange={event => setComment(event.target.value)} placeholder="填写核对依据或保留疑问"/></label></div><div className="adm-actions"><button className="adm-btn primary small" disabled={busy} onClick={() => void perform(() => saveReview({ action: 'classification', classification, comment }))}>保存分类</button><button className="adm-btn small" disabled={busy} onClick={() => void perform(() => saveReview({ action: 'visibility', visibility: data.visibility?.hidden ? 'visible' : 'hidden' }))}>{data.visibility?.hidden ? '人工恢复展示' : '人工隐藏内容'}</button><button className="adm-btn small secondary" disabled={busy || !review.record?.visibility_override} onClick={() => void perform(() => saveReview({ action: 'visibility', visibility: null }))}>清除展示覆盖</button></div></div><div className="adm-evidence-layout"><div>{Object.entries(sections).map(([section, title]) => <section key={section}><h3>{title}逐项核对</h3>{((data.recipe[section] || []) as Record<string, unknown>[]).map((item, index) => { const id = `${section}:${index}`; const original = section === 'steps' ? [item.action, item.heat, item.duration, item.tips].filter(Boolean).join(' · ') : [item.name, item.amount, item.note].filter(Boolean).join(' · '); return <ItemCheck key={`${selectedId}:${review.content_fingerprint}:${id}`} id={id} title={`${title} ${index + 1}${section === 'steps' ? ` · ${item.title || ''}` : ''}`} original={original} reason={review.ai_review?.item_checks?.find(check => check.id === id)?.reason} decision={review.item_decisions?.[id]} save={saveReview} disabled={busy}/>; })}</section>)}<button className="adm-btn primary" disabled={busy || review.manual_completed} onClick={() => void perform(async () => { await saveReview({ action: 'complete' }); setNotice('这条内容已完成人工核对'); })}>{review.manual_completed ? '已完成人工核对' : '完成这条人工核对'}</button><p className="adm-muted">菜谱需要逐项确认；技巧与非菜谱需要确认分类。意见独立保存。</p></div><aside className="adm-panel adm-transcript"><h3>原始字幕</h3><input aria-label="搜索字幕" placeholder="搜索字幕中的关键词" value={transcriptSearch} onChange={event => setTranscriptSearch(event.target.value)}/>{segments.filter(segment => !transcriptSearch || segment.text.includes(transcriptSearch)).map((segment, index) => <div key={index} className="adm-transcript-line">{source && <a href={`${source}${source.includes('?') ? '&' : '?'}t=${Math.floor(segment.start || segment.start_time || 0)}`} target="_blank" rel="noreferrer">{Math.floor(segment.start || segment.start_time || 0)} 秒 ↗</a>}<p>{segment.text}</p></div>)}{!segments.length && <p className="adm-muted">没有可用字幕，请对照原视频并记录核对依据。</p>}</aside></div></>}
    {tab === 'cover' && <div className="adm-panel"><h3>选择一张能看清成品的图片</h3><p className="adm-muted">选择候选后可拖动裁剪区域；确认前不会修改封面。</p><div className="adm-actions"><button className="adm-btn primary" disabled={busy} onClick={() => void perform(() => start('frames'))}>AI 选这道菜的特写</button><button className="adm-btn secondary" disabled={busy} onClick={() => void perform(() => capture(false))}>{busy ? '正在处理…' : '从视频生成手工候选'}</button><label className="adm-btn secondary">上传成品照片<input className="adm-visually-hidden" type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={event => { const file = event.target.files?.[0]; void perform(() => upload(file)); event.target.value = ''; }}/></label></div><details><summary>按视频时间精确截图</summary><div className="adm-actions"><label className="adm-field">视频位置（秒）<input type="number" min="0" step="0.1" value={timestamp} onChange={event => setTimestamp(Number(event.target.value))}/></label><button className="adm-btn" disabled={busy} onClick={() => void perform(() => capture(true))}>生成截图</button></div></details>{data.frame_candidate?.status === 'stale' && <p className="adm-notice">已有 AI 选图结果因菜谱、来源或选图策略变化已失效，请重新选图。</p>}{data.frame_candidate?.status === 'needs_review' && <p className="adm-notice">这张原帧尚未通过 AI 确认，请核对菜品和完成状态。选择候选并点击“确认裁剪并设为封面”后才会使用。</p>}{data.frame_candidate?.status === 'not_found' && <p className="adm-notice">AI 尚未找到合适特写，可调整选图参数或手工选图。</p>}<div className="adm-cover-candidates">{candidates.map(item => <button key={item.id} aria-label={item.label} className={candidate?.id === item.id ? 'selected' : ''} onClick={() => setCandidate(item)}><img src={item.url} alt={item.label} loading="lazy"/><span>{item.label}{item.timestamp != null ? ` · ${item.timestamp.toFixed(1)} 秒` : ''}</span></button>)}</div>{candidate && <CropEditor key={candidate.id} candidate={candidate} busy={busy} save={crop => void perform(() => saveCover(candidate.id, crop, candidate.candidate_revision))}/>}<div className="adm-cover-unavailable"><button className="adm-btn secondary" disabled={busy} onClick={() => void perform(() => saveCover('unavailable'))}>暂无合适图片，先留空</button></div></div>}
    {tab === 'history' && <div className="adm-panel"><h3>独立审核历史</h3><p className="adm-muted">保留历次变更前的记录，方便核对；不会自动恢复或覆盖原菜谱。</p>{history.length ? history.map((item, index) => <details key={index}><summary>{String(item.archived_at || '')} · {String(item.event || '')}</summary><pre className="adm-code">{JSON.stringify(item.review, null, 2)}</pre></details>) : <div className="adm-empty">还没有历史审核记录。</div>}</div>}</>}
    </section></div></div>;
}
