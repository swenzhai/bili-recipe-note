import { useEffect, useRef, useState } from 'react';
import { api, post } from './api';
import type { PageProps, TaskSummary } from './types';
import './admin-tasks.css';

type Creator = { id: string; name: string; count: number; updated_at?: string; urls: string[]; source_url?: string; complete?: boolean; warnings?: string[] };
function errorText(error: unknown) { return error instanceof Error ? error.message : String(error); }

export default function AcquisitionPage({ navigate }: PageProps) {
  const [urls, setUrls] = useState('');
  const [creators, setCreators] = useState<Creator[]>([]);
  const [creatorUrl, setCreatorUrl] = useState('');
  const [creatorName, setCreatorName] = useState('');
  const [stage, setStage] = useState('recipe');
  const [createOnly, setCreateOnly] = useState(false);
  const [provider, setProvider] = useState('');
  const [model, setModel] = useState('');
  const [whisper, setWhisper] = useState('');
  const [screenshots, setScreenshots] = useState(true);
  const [requireLlm, setRequireLlm] = useState(false);
  const [steps, setSteps] = useState(10);
  const [images, setImages] = useState(3);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const urlsInput = useRef<HTMLTextAreaElement>(null);
  const uniqueUrls = [...new Set(urls.split(/\r?\n/).map(value => value.trim()).filter(value => value && !value.startsWith('#')))];

  useEffect(() => {
    const controller = new AbortController();
    void api<{ items: Creator[] }>('/creators', { signal: controller.signal })
      .then(result => { setCreators(result.items); setLoading(false); })
      .catch(err => { if (!controller.signal.aborted) { setError(errorText(err)); setLoading(false); } });
    return () => controller.abort();
  }, [refresh]);

  async function importFile(file: File | undefined) {
    if (!file) return;
    setError('');
    try {
      if (file.size > 2 * 1024 * 1024) throw new Error('链接清单最大支持 2 MB，请拆分后导入。');
      const content = (await file.text()).replace(/^\uFEFF/, '');
      setUrls(value => [value.trim(), content.trim()].filter(Boolean).join('\n'));
      setNotice(`已导入 ${file.name}。可以在左侧删除本轮不需要的视频链接。`);
      urlsInput.current?.focus();
    } catch (err) { setError(errorText(err)); }
  }
  async function start() {
    setError(''); setBusy(true);
    try {
      const options: Record<string, unknown> = { no_screenshot: !screenshots, max_recipe_steps: steps, max_step_images: images, require_llm: requireLlm,
        ...(provider ? { llm_provider: provider, no_llm_summary: provider === 'none' } : {}),
        ...(model.trim() && ['codex', 'openai'].includes(provider) ? { [provider === 'codex' ? 'codex_model' : 'openai_model']: model.trim() } : {}),
        ...(whisper.trim() ? { whisper_model: whisper.trim() } : {}) };
      const task = await post<TaskSummary>('/tasks', { kind: 'generation', urls: uniqueUrls, target_stage: stage, create_only: createOnly, creator_name: creatorName.trim() || null, options });
      navigate(`/tasks?kind=generation&id=${encodeURIComponent(task.id)}`);
    } catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }
  async function refreshCreator(value: string) {
    setError(''); setBusy(true);
    try {
      const task = await post<TaskSummary>('/creators/refresh', { url: value });
      navigate(`/tasks?kind=${task.kind}&id=${encodeURIComponent(task.id)}`);
    } catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }

  return <div className="adm-page">
    <header className="adm-page-head"><div><p className="adm-eyebrow">把好手艺，留在自家厨房</p><h1>采集与生成</h1><p className="adm-muted">导入烹饪视频，自动整理字幕、用料和做法。长任务在后台进行。</p></div><button className="adm-btn secondary" onClick={() => navigate('/tasks')}>查看任务进度</button></header>
    {error && <div role="alert" className="adm-error">{error}</div>}{notice && <div role="status" className="adm-notice">{notice}</div>}
    <div className="adm-import-grid"><form className="adm-panel" onSubmit={event => { event.preventDefault(); void start(); }}>
      <h2>从视频开始</h2><label className="adm-field">视频链接（一行一个）<textarea ref={urlsInput} value={urls} onChange={event => setUrls(event.target.value)} placeholder={'https://www.bilibili.com/video/BV…\n也支持 BV 号和 b23.tv 分享链接'} required /></label>
      <p className="adm-muted">已填写 {uniqueUrls.length} 条；重复链接会自动合并。分 P 视频保留各自分段。</p>
      <label className="adm-import-file">或者导入已有链接清单<input type="file" accept=".txt,text/plain" onChange={event => { void importFile(event.target.files?.[0]); event.target.value = ''; }} /></label>
      <div className="adm-form-grid"><label className="adm-field">整理到哪一步<select value={stage} onChange={event => setStage(event.target.value)}><option value="recipe">完整菜谱：字幕、用料、做法与配图</option><option value="raw">先采集原始资料：来源与字幕</option></select></label><label className="adm-field">UP 主名称（可选）<input value={creatorName} maxLength={200} placeholder="默认使用视频作者名称" onChange={event => setCreatorName(event.target.value)} /></label></div>
      <details><summary>生成设置</summary><div className="adm-form-grid">
        <label className="adm-field">文本模型服务<select value={provider} onChange={event => setProvider(event.target.value)}><option value="">使用当前设置</option><option value="opencode">OpenCode</option><option value="codex">Codex CLI</option><option value="openai">OpenAI API</option><option value="local">本地命令</option><option value="none">仅使用规则提取</option></select></label>
        {['codex', 'openai'].includes(provider) && <label className="adm-field">模型名称<input value={model} onChange={event => setModel(event.target.value)} placeholder="留空沿用当前设置" /></label>}
        <label className="adm-field">无字幕时的转写模型<input value={whisper} onChange={event => setWhisper(event.target.value)} placeholder="留空使用当前设置，如 small" /></label>
        <label className="adm-field">关键步骤数<input type="number" min="4" max="12" value={steps} onChange={event => setSteps(Number(event.target.value))} /></label>
        <label className="adm-field">步骤图片数<input type="number" min="1" max="4" value={images} onChange={event => setImages(Number(event.target.value))} /></label></div>
        <label className="adm-task-check"><input type="checkbox" checked={screenshots} onChange={event => setScreenshots(event.target.checked)} />生成步骤图片</label>
        <label className="adm-task-check"><input type="checkbox" checked={requireLlm} onChange={event => setRequireLlm(event.target.checked)} />模型失败时保留失败状态，便于重试</label>
      </details>
      <label className="adm-task-check"><input type="checkbox" checked={createOnly} onChange={event => setCreateOnly(event.target.checked)} />先保存为待执行任务，稍后开始</label>
      <div className="adm-actions"><button type="submit" className="adm-btn primary" disabled={busy || !uniqueUrls.length}>{busy ? '正在安排…' : createOnly ? `保存 ${uniqueUrls.length} 条待执行任务` : `开始整理 ${uniqueUrls.length} 个视频`}</button></div>
    </form>
      <aside><section className="adm-panel"><h2>跟进常看的 UP 主</h2><p className="adm-muted">更新的视频链接按 UP 主保存在清单中。更新完成后载入清单，再决定本轮生成哪些菜谱。</p>
        <form onSubmit={event => { event.preventDefault(); void refreshCreator(creatorUrl); }}><label className="adm-field">UP 主主页<input type="url" value={creatorUrl} onChange={event => setCreatorUrl(event.target.value)} placeholder="https://space.bilibili.com/123456/video" required /></label><div className="adm-actions"><button className="adm-btn secondary" type="submit" disabled={busy || !creatorUrl.trim()}>后台更新链接清单</button></div></form></section>
        <section className="adm-panel"><div className="adm-task-card-head"><h2>已保存的链接清单</h2><button className="adm-btn small" onClick={() => setRefresh(value => value + 1)}>刷新</button></div>
          {loading ? <p className="adm-muted">正在读取清单…</p> : !creators.length ? <div className="adm-empty"><p>还没有保存的 UP 主。填写主页即可建立第一份清单。</p></div> : <div className="adm-creator-list">{creators.map(creator => <div className="adm-creator-item" key={creator.id}><strong>{creator.name}</strong><p className="adm-muted">{creator.count} 个视频{creator.updated_at ? ` · ${new Date(creator.updated_at).toLocaleDateString('zh-CN')} 更新` : ''}</p>{creator.complete === false && <p className="adm-notice">上次抓取未完整，建议重新更新。</p>}<div className="adm-actions"><button className="adm-btn secondary small" onClick={() => { setUrls(creator.urls.join('\n')); setCreatorName(creator.name); setNotice(`已载入 ${creator.count} 条链接；可在文本框中删除不需要的视频。`); urlsInput.current?.focus(); }}>载入清单</button>{creator.source_url && <button className="adm-btn small" disabled={busy} onClick={() => void refreshCreator(creator.source_url!)}>更新</button>}</div></div>)}</div>}
        </section></aside>
    </div>
  </div>;
}
