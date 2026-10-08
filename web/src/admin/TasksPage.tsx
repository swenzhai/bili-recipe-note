import { useEffect, useState } from 'react';
import { api, ApiError, post } from './api';
import type { PageProps, TaskSummary } from './types';
import './admin-tasks.css';

const kinds: Record<string, string> = { generation: '采集与生成', review: 'AI 审核', frames: 'AI 选图', 'dish-info': '菜品介绍', creators: 'UP 主更新', settings: '数据与知识库' };
const statuses: Record<string, string> = { pending: '待开始', queued: '排队中', running: '进行中', pausing: '完成当前条后暂停', paused: '已暂停', interrupted: '运行中断', completed: '已完成', completed_with_errors: '部分失败', failed: '失败', skipped: '已跳过', not_found: '无匹配画面', needs_review: '候选待确认', done: '已完成', raw_ready: '原始资料就绪' };
const activeStates = new Set(['running', 'queued', 'pausing']);
const aiKinds = new Set(['frames', 'review', 'dish-info']);
type Task = TaskSummary & { target_stage?: string; can_generate_recipe?: boolean; result?: { url?: string; name?: string } };
type ConsolidationResult = { groups: number; removed_tasks: number; recovered_items: number; removed_files: number; skipped_groups: number };

function duration(value: number | null | undefined) {
  if (value == null) return '待估算';
  if (value < 60) return '不到 1 分钟';
  const minutes = Math.round(value / 60);
  return minutes < 60 ? `${minutes} 分钟` : `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
}
function errorText(error: unknown) { return error instanceof Error ? error.message : String(error); }

export function queueOptions(kind: string, limit: number, runLimit: number, createOnly: boolean) {
  return { limit, ...(aiKinds.has(kind) ? { run_limit: runLimit, create_only: createOnly } : {}) };
}

export function controlOptions(kind: string, action: string, runLimit: number, useCurrent: boolean) {
  const execution = (aiKinds.has(kind) && ['resume', 'retry'].includes(action)) || (kind === 'frames' && action === 'retry-not-found');
  return { use_current_config: useCurrent, ...(execution ? { run_limit: runLimit } : {}) };
}

export function matchesTaskFilter(task: TaskSummary, filter: string) {
  return filter === 'all' || (filter === 'active' && activeStates.has(task.status)) ||
    (filter === 'attention' && (task.failed > 0 || (task.needs_review || 0) > 0 || (task.not_found || 0) > 0 || ['paused', 'interrupted', 'failed'].includes(task.status))) ||
    (filter === 'completed' && task.status === 'completed') ||
    (filter === 'pending' && (task.status === 'pending' || (task.pending || 0) > 0));
}

export function taskExecutionLabels(task: TaskSummary) {
  const aiTask = aiKinds.has(task.kind);
  const joinsPending = aiTask && (task.pending || 0) > 0;
  return {
    eta: aiTask && task.run_limit != null ? '预计本批剩余' : '预计剩余',
    retry: `${joinsPending ? '加入失败项并执行下一批' : '重试失败项'}（${task.failed}）`,
    retryHint: joinsPending ? '失败项会重新加入待执行列表，与原待办共同计入本批执行数量。' : '',
  };
}

export function GenerationDishes({ item }: { item: Record<string, unknown> }) {
  const dishes = Array.isArray(item.dishes) ? item.dishes as Record<string, unknown>[] : [];
  if (!dishes.length) return item.dish_plan_error ? <p className="adm-error">{String(item.dish_plan_error)}</p> : null;
  return <div><p>本视频 {dishes.length} 道菜，已完成 {Number(item.completed_dish_count || 0)} 道</p><ul>{dishes.map(dish => <li key={String(dish.dish_id)}>{String(dish.title || '未命名菜谱')} · {statuses[String(dish.status)] || String(dish.status || 'pending')}{Boolean(dish.error) && <span className="adm-error"> · {String(dish.error)}</span>}</li>)}</ul></div>;
}

export default function TasksPage({ navigate }: PageProps) {
  const query = new URLSearchParams(window.location.search);
  const selectedKey = query.get('kind') && query.get('id') ? `${query.get('kind')}/${query.get('id')}` : '';
  const [tasks, setTasks] = useState<Task[]>([]);
  const [detail, setDetail] = useState<Task | null>(null);
  const [filter, setFilter] = useState(query.get('filter') || 'all');
  const [kindFilter, setKindFilter] = useState('all');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState(query.get('reused') === 'completed' ? '这批视频已有完整结果，已打开原任务。' : query.get('reused') === 'pending' ? '已复用原任务，继续处理未完成视频。' : query.get('existing') === '1' ? '已有同类任务正在运行，本次没有创建新任务。' : '');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [consolidating, setConsolidating] = useState(false);
  const [revision, setRevision] = useState(0);
  const [showCreate, setShowCreate] = useState(Boolean(query.get('create')));
  const [kind, setKind] = useState(query.get('create') || 'frames');
  const [limit, setLimit] = useState(0);
  const [runLimit, setRunLimit] = useState(Math.min(10, (query.get('recipe_ids') || query.get('ids') || '').split(',').filter(Boolean).length || 10));
  const [nextRun, setNextRun] = useState<{ key: string; value: number } | null>(null);
  const [itemFilter, setItemFilter] = useState('all');
  const [onlyPending, setOnlyPending] = useState(true);
  const [webSearch, setWebSearch] = useState(false);
  const [provider, setProvider] = useState('');
  const [model, setModel] = useState('');
  const [requirement, setRequirement] = useState('找到一张制作完成的菜品特写，主体清晰；优先选择无遮挡、构图完整的画面，手、餐具或字幕仅在遮挡菜品主体时影响评价。');
  const [frameModel, setFrameModel] = useState('');
  const [python, setPython] = useState('');
  const [device, setDevice] = useState('cpu');
  const [threads, setThreads] = useState(16);
  const [coarse, setCoarse] = useState(24);
  const [fine, setFine] = useState(8);
  const [keepVideo, setKeepVideo] = useState(false);
  const [skipConfirmed, setSkipConfirmed] = useState(true);
  const [useCurrent, setUseCurrent] = useState(false);
  const recipeIds = (query.get('recipe_ids') || query.get('ids') || '').split(',').filter(Boolean);
  const nextRunLimit = nextRun?.key === selectedKey ? nextRun.value : detail?.run_limit === undefined ? 10 : detail.run_limit || 0;
  const invalidRunLimit = detail && aiKinds.has(detail.kind) && (!Number.isInteger(nextRunLimit) || nextRunLimit < 0 || nextRunLimit > 10000);
  const executionLabels = detail ? taskExecutionLabels(detail) : null;

  useEffect(() => {
    const create = new URLSearchParams(window.location.search).get('create');
    if (create) {
      setShowCreate(true); setKind(create);
      const ids = (new URLSearchParams(window.location.search).get('recipe_ids') || new URLSearchParams(window.location.search).get('ids') || '').split(',').filter(Boolean);
      setLimit(0); setRunLimit(Math.min(10, ids.length || 10));
    }
  }, [window.location.search]);

  useEffect(() => {
    let cancelled = false;
    let inFlight = false;
    const controller = new AbortController();
    const refresh = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        let removed = false;
        const [listing, selected] = await Promise.all([
          api<{ items: Task[] }>('/tasks', { signal: controller.signal }),
          selectedKey ? api<Task>(`/tasks/${selectedKey}`, { signal: controller.signal }).catch(error => {
            if (error instanceof ApiError && error.status === 404) { removed = true; return null; }
            throw error;
          }) : Promise.resolve(null),
        ]);
        if (!cancelled) {
          setTasks(listing.items); setDetail(selected); setError('');
          if (removed) { setNotice('原任务记录已整理或移除，请从列表查看保留的最终结果。'); navigate('/tasks'); }
        }
      } catch (err) { if (!cancelled) setError(errorText(err)); }
      finally { inFlight = false; if (!cancelled) setLoading(false); }
    };
    void refresh();
    const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, 5000);
    const visible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener('visibilitychange', visible);
    return () => { cancelled = true; controller.abort(); window.clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [selectedKey, revision]);

  async function control(task: Task, action: string, extra: Record<string, unknown> = {}) {
    setBusy(true); setError(''); setNotice('');
    try {
      const updated = await post<Task>(`/tasks/${task.kind}/${task.id}/${action}`, { ...controlOptions(task.kind, action, nextRunLimit, useCurrent), ...extra });
      setDetail(updated); setRevision(value => value + 1);
      setNotice(updated.already_running ? '已有同类任务正在运行，已打开现有任务。' : action === 'pause' && task.kind !== 'generation' ? '已请求暂停；当前菜品处理完成后生效。' : '任务状态已更新。');
      navigate(`/tasks?kind=${updated.kind}&id=${encodeURIComponent(updated.id)}`);
    } catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }
  async function consolidate() {
    setBusy(true); setConsolidating(true); setError(''); setNotice('');
    try {
      const result = await api<ConsolidationResult>('/tasks/consolidate', { method: 'POST' });
      setDetail(null); setRevision(value => value + 1);
      setNotice(`${result.groups ? `已整理 ${result.groups} 组任务，清理 ${result.removed_tasks} 条重复记录，补回 ${result.recovered_items} 个已成功的视频结果。` : '没有需要整理的重复生成任务。'}${result.skipped_groups ? `另有 ${result.skipped_groups} 组暂时保留，可稍后再整理。` : ''}`);
      navigate('/tasks');
    } catch (err) { setError(errorText(err)); }
    finally { setBusy(false); setConsolidating(false); }
  }
  async function create(createOnly = false) {
    setBusy(true); setError(''); setNotice('');
    try {
      const options: Record<string, unknown> = kind === 'frames'
        ? { requirement, device, threads, coarse_limit: coarse, fine_limit: fine, keep_video: keepVideo, skip_confirmed: skipConfirmed,
            ...(frameModel.trim() ? { model: frameModel.trim() } : {}), ...(python.trim() ? { python_executable: python.trim() } : {}) }
        : { ...(provider ? { provider } : {}), ...(kind === 'dish-info' ? { web_search: webSearch } : {}),
            ...(model.trim() && ['codex', 'openai'].includes(provider) ? { [provider === 'codex' ? 'codex_model' : 'openai_model']: model.trim() } : {}) };
      const task = await post<Task>('/tasks', { kind, ...queueOptions(kind, limit, runLimit, createOnly), only_pending: onlyPending, options, ...(recipeIds.length ? { recipe_ids: recipeIds } : {}) });
      setShowCreate(false); setDetail(task); setRevision(value => value + 1);
      setNotice(task.already_running ? '已有同类任务正在运行，本次没有创建新任务。' : task.total === 0 ? '当前范围没有需要处理的菜谱。可以调整范围或取消跳过有效结果。' : createOnly ? `已将 ${task.total} 条加入待执行列表，稍后可执行首批。` : `已将 ${task.total} 条加入列表并在后台执行首批，关闭页面后继续运行。`);
      navigate(`/tasks?kind=${task.kind}&id=${encodeURIComponent(task.id)}`);
    } catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }
  const visible = tasks.filter(task => (kindFilter === 'all' || task.kind === kindFilter) && matchesTaskFilter(task, filter));
  const visibleItems = detail?.items?.filter(item => itemFilter === 'all' || item.status === itemFilter) || [];

  return <div className="adm-page">
    <header className="adm-page-head"><div><p className="adm-eyebrow">有条不紊地准备每一道菜</p><h1>任务中心</h1><p className="adm-muted">所有后台任务都在这里。进度每 5 秒更新，离开页面也会继续处理。</p></div>
      <div className="adm-actions"><button className="adm-btn secondary" onClick={() => navigate('/import')}>采集视频</button><button className="adm-btn primary" onClick={() => setShowCreate(value => !value)}>新建 AI 任务</button></div></header>
    {error && <div className="adm-error" role="alert">{error}<button className="adm-btn small" onClick={() => setRevision(value => value + 1)}>重试加载</button></div>}
    {notice && <div className="adm-notice" role="status">{notice}</div>}

    {showCreate && <form className="adm-panel adm-task-create" onSubmit={event => { event.preventDefault(); void create((event.nativeEvent as SubmitEvent).submitter?.getAttribute('value') === 'queue'); }}>
      <div className="adm-task-card-head"><h2>安排一项 AI 任务</h2><button type="button" className="adm-btn small" onClick={() => setShowCreate(false)}>收起</button></div>
      <p className="adm-muted">{recipeIds.length ? `已选择 ${recipeIds.length} 道菜谱。` : '审核覆盖菜谱库；选图与介绍默认处理可展示且已上架的菜谱。'}</p>
      <div className="adm-form-grid"><label className="adm-field">任务类型<select value={kind} onChange={event => setKind(event.target.value)}><option value="frames">AI 选取成品特写</option><option value="review">AI 审核与归类</option><option value="dish-info">补充菜品介绍</option></select></label>
        <label className="adm-field">加入列表数量（0 表示全部）<input type="number" min="0" max="10000" value={limit} onChange={event => setLimit(Number(event.target.value))} /></label>
        <label className="adm-field">本批执行数量（0 表示全部）<input type="number" min="0" max="10000" value={runLimit} onChange={event => setRunLimit(Number(event.target.value))} /></label></div>
      <p className="adm-muted">默认将当前范围全部加入列表，先执行 10 条；达到本批数量后自动暂停，其余条目保留待执行。可在任务详情调整下一批数量并继续。</p>
      <label className="adm-task-check"><input type="checkbox" checked={onlyPending} onChange={event => setOnlyPending(event.target.checked)} />跳过已有有效结果</label>
      {kind === 'frames' ? <><label className="adm-field">想选取什么画面<textarea value={requirement} required rows={2} onChange={event => setRequirement(event.target.value)} /></label>
        <label className="adm-task-check"><input type="checkbox" checked={skipConfirmed} onChange={event => setSkipConfirmed(event.target.checked)} />保留已人工确认的封面</label>
        <p className="adm-muted">生成的候选图会等待人工裁剪与确认；“无匹配画面”会单独记录。</p>
        <details><summary>视觉模型与采样设置</summary><div className="adm-form-grid"><label className="adm-field">本地视觉模型目录<input value={frameModel} placeholder="留空使用已配置模型" onChange={event => setFrameModel(event.target.value)} /></label><label className="adm-field">选帧 Python 解释器<input value={python} placeholder="留空使用独立选帧环境" onChange={event => setPython(event.target.value)} /></label><label className="adm-field">运行设备<select value={device} onChange={event => setDevice(event.target.value)}><option value="cpu">CPU</option><option value="cuda">CUDA</option></select></label><label className="adm-field">线程数<input type="number" min="1" value={threads} onChange={event => setThreads(Number(event.target.value))} /></label><label className="adm-field">粗筛候选帧数<input type="number" min="2" value={coarse} onChange={event => setCoarse(Number(event.target.value))} /></label><label className="adm-field">精筛候选帧数<input type="number" min="1" value={fine} onChange={event => setFine(Number(event.target.value))} /></label></div><label className="adm-task-check"><input type="checkbox" checked={keepVideo} onChange={event => setKeepVideo(event.target.checked)} />保留本次下载的视频，便于后续手工选图</label></details></>
        : <><div className="adm-form-grid"><label className="adm-field">模型服务<select value={provider} onChange={event => setProvider(event.target.value)}><option value="">使用设置中的服务</option><option value="codex">Codex CLI</option><option value="opencode">OpenCode</option><option value="openai">OpenAI API</option><option value="local">本地命令</option></select></label>{['codex', 'openai'].includes(provider) && <label className="adm-field">模型名称<input value={model} placeholder="留空沿用设置" onChange={event => setModel(event.target.value)} /></label>}</div>
          {kind === 'dish-info' && <><label className="adm-task-check"><input type="checkbox" checked={webSearch} onChange={event => setWebSearch(event.target.checked)} />联网查找背景与起源</label><p className="adm-muted">联网结果保留出处和匹配提示；无法对应本菜时不补写背景。搜索失败仍会生成本菜简介。</p></>}</>}
      <div className="adm-actions"><button type="submit" value="run" className="adm-btn primary" disabled={busy}>{busy ? '正在安排…' : '加入并执行首批'}</button><button type="submit" value="queue" className="adm-btn secondary" disabled={busy}>仅加入待执行列表</button></div>
    </form>}

    <div className="adm-task-layout"><section aria-label="任务列表">
      <div className="adm-task-filters"><label className="adm-field">任务状态<select value={filter} onChange={event => { setFilter(event.target.value); }}><option value="all">全部任务</option><option value="active">进行中</option><option value="attention">需要处理</option><option value="pending">有待执行条目</option><option value="completed">已完成</option></select></label><label className="adm-field">任务类型<select value={kindFilter} onChange={event => setKindFilter(event.target.value)}><option value="all">所有类型</option>{Object.entries(kinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><button className="adm-btn small" onClick={() => setRevision(value => value + 1)}>刷新</button><button className="adm-btn small" disabled={busy} onClick={() => void consolidate()}>{consolidating ? '正在整理…' : '整理重复任务'}</button></div>
      <p className="adm-muted">生成任务可按每个视频的最终结果整理：保留有效成功结果和仍需处理的失败，清理重复重试记录。</p>
      {loading && !tasks.length ? <div className="adm-empty" role="status">正在读取任务…</div> : !visible.length ? <div className="adm-empty"><h3>{tasks.length ? '没有符合筛选的任务' : '厨房已经准备就绪'}</h3><p>{tasks.length ? '试试切换任务类型或状态。' : '导入一段烹饪视频，或给已有菜谱安排 AI 选图与审核。'}</p></div> :
        <div className="adm-task-list">{visible.map(task => <button key={`${task.kind}/${task.id}`} className={`adm-panel adm-task-card ${selectedKey === `${task.kind}/${task.id}` ? 'selected' : ''}`} onClick={() => navigate(`/tasks?kind=${task.kind}&id=${encodeURIComponent(task.id)}`)}>
          <div className="adm-task-card-head"><strong>{task.title}</strong><span className={`adm-badge ${task.failed ? 'danger' : task.status === 'completed' ? 'success' : activeStates.has(task.status) ? 'warning' : ''}`}>{statuses[task.status] || task.status}</span></div>
          <progress className="adm-progress" value={task.processed} max={task.total || 1} aria-label={`${task.title}进度`} />
          <div className="adm-task-card-head"><span>{task.processed} / {task.total} 条</span><span className="adm-muted">{Math.round(task.progress * 100)}%</span></div>
          {aiKinds.has(task.kind) && <p className="adm-muted">待执行 {task.pending ?? Math.max(0, task.total - task.processed)} · 本批已处理 {task.run_processed || 0}{task.run_limit ? ` / ${task.run_limit}` : ''}</p>}
          {task.current && <p className="adm-task-current">正在处理：{task.current}</p>}
          <p className="adm-muted adm-task-counts">成功 {task.completed} · 失败 {task.failed} · 跳过 {task.skipped}{task.not_found ? ` · 无匹配 ${task.not_found}` : ''}{task.needs_review ? ` · 待确认 ${task.needs_review}` : ''}</p>
          <small className="adm-muted">{task.updated_at ? new Date(task.updated_at).toLocaleString('zh-CN') : task.id}</small>
        </button>)}</div>}
    </section>
      <aside className="adm-panel adm-task-detail" aria-label="任务详情">{detail ? <>
        <div className="adm-task-card-head"><h2>{detail.title}</h2><span className="adm-badge">{statuses[detail.status] || detail.status}</span></div><p className="adm-muted">{detail.id}</p>
        <progress className="adm-progress" max={detail.total || 1} value={detail.processed} aria-label="所选任务进度" /><p>已处理 {detail.processed} / {detail.total} 条</p>
        {aiKinds.has(detail.kind) && <p>列表总量 {detail.total} · 待执行 {detail.pending ?? Math.max(0, detail.total - detail.processed)} · 本批已处理 {detail.run_processed || 0}{detail.run_limit ? ` / ${detail.run_limit}` : ''}</p>}
        {detail.pause_reason === 'retry_scope_completed' && <p className="adm-notice">本轮无匹配重试已结束，原待执行条目已保留。点击“执行下一批待办”可继续原列表。</p>}
        {detail.run_scope === 'not_found' && <p className="adm-muted">本轮仅重试无匹配项，不处理原待执行列表。</p>}
        {Boolean(detail.needs_review) && <p className="adm-notice">有 {detail.needs_review} 张 AI 候选待确认，请在对应菜谱的“审核工作台 → 成品图”查看原帧并决定是否裁剪使用。</p>}
        {detail.pause_reason === 'batch_limit' && <p className="adm-notice">本批数量已完成，其余条目保留待执行。调整下批执行数量后，可继续处理。</p>}
        <div className="adm-task-time"><div><span className="adm-muted">已用时间</span><strong>{duration(detail.elapsed_seconds)}</strong></div><div><span className="adm-muted">{executionLabels?.eta}</span><strong>{duration(detail.eta_seconds)}</strong></div></div>
        {detail.current && <p className="adm-task-current">当前：{detail.current}</p>}
        {detail.current_progress && <div className="adm-notice">{Object.entries(detail.current_progress).filter(([, value]) => ['string', 'number'].includes(typeof value)).map(([key, value]) => <span key={key} className="adm-task-stage">{({ stage: '阶段', phase: '阶段', completed: '已完成', total: '总数', current: '当前', message: '状态', coarse: '粗筛', fine: '精筛' } as Record<string, string>)[key] || key}：{String(value)} </span>)}</div>}
        {detail.error && <p className="adm-error">{detail.error}</p>}
        {aiKinds.has(detail.kind) && (detail.can_resume || detail.can_retry || detail.can_retry_not_found) && <label className="adm-field">下批执行数量（0 表示全部）<input type="number" min="0" max="10000" step="1" value={nextRunLimit} onChange={event => setNextRun({ key: selectedKey, value: Number(event.target.value) })} /></label>}
        {(detail.can_pause || detail.can_resume || detail.can_retry || detail.can_retry_not_found || detail.can_generate_recipe) && <div className="adm-actions">
          {detail.can_pause && <button className="adm-btn secondary" disabled={busy} onClick={() => void control(detail, 'pause')}>{detail.kind === 'generation' ? '立即暂停' : '完成当前条后暂停'}</button>}
          {detail.can_resume && <button className="adm-btn primary" disabled={busy || Boolean(invalidRunLimit)} onClick={() => void control(detail, 'resume')}>{aiKinds.has(detail.kind) ? '执行下一批待办' : '继续未完成'}</button>}
          {detail.can_retry && <button className="adm-btn secondary" disabled={busy || Boolean(invalidRunLimit)} onClick={() => void control(detail, 'retry')}>{executionLabels?.retry}</button>}
          {detail.kind === 'frames' && detail.can_retry_not_found && <button className="adm-btn secondary" disabled={busy || Boolean(invalidRunLimit)} onClick={() => void control(detail, 'retry-not-found')}>重试无匹配（{detail.not_found || 0}）</button>}
          {detail.can_generate_recipe && <button className="adm-btn primary" disabled={busy} onClick={() => void control(detail, 'resume', { target_stage: 'recipe' })}>继续生成完整菜谱</button>}
        </div>}
        {detail.kind === 'frames' && detail.can_retry_not_found && <p className="adm-muted">“重试无匹配”仅重选没有候选的条目，按下批数量执行；原待执行条目和待人工确认的图片保持不动。</p>}
        {detail.can_retry && executionLabels?.retryHint && <p className="adm-muted">{executionLabels.retryHint}</p>}
        {(detail.can_resume || detail.can_retry || detail.can_retry_not_found) && !['creators', 'settings'].includes(detail.kind) && <label className="adm-task-check"><input type="checkbox" checked={useCurrent} onChange={event => setUseCurrent(event.target.checked)} />续跑使用当前设置（默认沿用原任务）</label>}
        {detail.result?.url && <a className="adm-btn primary" href={detail.result.url} download>下载{detail.result.name || '结果'}</a>}
        <details open={Boolean(detail.failed)}><summary>{detail.kind === 'generation' ? '视频最终结果' : '逐条结果与错误'}（{detail.items?.length || 0}）</summary>{aiKinds.has(detail.kind) && <label className="adm-field">条目状态<select value={itemFilter} onChange={event => setItemFilter(event.target.value)}><option value="all">全部条目</option><option value="pending">待执行</option><option value="failed">失败</option>{detail.kind === 'frames' && <><option value="not_found">无匹配</option><option value="needs_review">候选待确认</option></>}</select></label>}<div className="adm-task-results">{(aiKinds.has(detail.kind) ? visibleItems : detail.items)?.map((item, index) => <div key={index} className="adm-task-result"><strong>{String(item.folder || item.url || item.id || `条目 ${index + 1}`)}</strong><span className="adm-badge">{statuses[String(item.status)] || String(item.status)}</span>{Boolean(item.error) && <p className="adm-error">{String(item.error)}</p>}{detail.kind === 'generation' && <GenerationDishes item={item} />}{Array.isArray(item.warnings) && item.warnings.length > 0 && <p className="adm-notice">{item.warnings.join('；')}</p>}{item.complete === false && <p className="adm-notice">该 UP 主链接可能未抓取完整，请检查日志后重新更新。</p>}</div>)}</div></details>
        {(detail.log?.trim() || detail.status !== 'completed') && <details><summary>运行日志</summary><pre className="adm-task-log">{detail.log || '暂无日志。任务执行后会在这里显示。'}</pre></details>}
        <details><summary>本次参数</summary><pre className="adm-task-log">{JSON.stringify(detail.options || {}, null, 2)}</pre></details>
      </> : <div className="adm-empty"><h3>每一步，都心中有数</h3><p>选择左侧任务，查看阶段进度、处理结果和运行日志。</p></div>}</aside>
    </div>
  </div>;
}
