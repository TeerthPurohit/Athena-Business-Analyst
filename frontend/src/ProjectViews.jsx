import { useEffect, useState } from 'react'
import { ArrowRight, Check, CircleHelp, Download, Eye, FileCheck2, FilePlus2, FolderOpen, LoaderCircle, Paperclip, Search } from 'lucide-react'
import { apiUrl } from './api'

const humanize = (key) => key.replaceAll('_', ' ')
const deliverableFormat = (format) => {
  const value = format?.toLowerCase()
  return value === 'pdf' ? 'PDF' : value === 'xlsx' ? 'Excel workbook' : 'Markdown'
}
const isFilled = (value) => value != null && value !== '' && (!Array.isArray(value) || value.length > 0)
const formatValue = (value) => Array.isArray(value) ? value.map(formatValue).join('; ')
  : value && typeof value === 'object' ? Object.entries(value).filter(([, item]) => isFilled(item)).map(([key, item]) => `${humanize(key)}: ${formatValue(item)}`).join(', ')
    : String(value)

// Business context entries as [label, text | null]. Entity sections (stakeholders, processes, risks, …)
// are lists of records labelled by their identifying field; other sections are field maps.
export function contextEntries(section) {
  if (Array.isArray(section)) return section.map((record, index) => {
    const [first, ...rest] = Object.entries(record || {}).filter(([, value]) => isFilled(value))
    return [first ? formatValue(first[1]) : `Item ${index + 1}`, rest.map(([key, value]) => `${humanize(key)}: ${formatValue(value)}`).join('; ')]
  })
  return Object.entries(section || {}).map(([field, value]) => [humanize(field), isFilled(value) ? formatValue(value) : null])
}

export function ProjectOverview({
  project, sources, findings, documents, failedSourceIds, uploadReport, feedback, busy, fileRef,
  onNavigate, onUpload, prompt, onPromptChange, analysisMode, onModeChange,
  onSubmitPrompt, activity,
}) {
  const sourceFiles = sources.filter((source) => source.kind === 'document')
  const steps = [
    { id: 'workspace', title: 'Add project material', detail: 'Upload a source or describe a confirmed decision.', count: `${sourceFiles.length} ${sourceFiles.length === 1 ? 'source' : 'sources'}`, icon: FolderOpen },
    { id: 'clarifications', title: 'Answer questions', detail: 'Fill in details the source material does not explain.', count: 'Find next question', icon: CircleHelp },
    { id: 'deliverables', title: 'Create a document', detail: 'Make a draft from the project record, then read and download it.', count: `${documents.length} ${documents.length === 1 ? 'draft' : 'drafts'}`, icon: FileCheck2 },
  ]

  return (
    <section className="flow-page">
      <div className="flow-heading">
        <span className="flow-eyebrow">Project overview</span>
        <h1>{project.name}</h1>
        <p>Start with your material. Athena extracts validated findings, surfaces questions to answer, and drafts documents from your project record.</p>
      </div>

      <div className="flow-steps" aria-label="How this project works">
        {steps.map(({ id, title, detail, count, icon: Icon }, index) => (
          <button className="flow-step" key={id} type="button" onClick={() => id === 'workspace' ? fileRef.current?.click() : onNavigate(id)} disabled={!!busy}>
            <span className="flow-step-number">{index + 1}</span>
            <span className="flow-step-icon"><Icon size={20} /></span>
            <span className="flow-step-copy"><strong>{title}</strong><small>{detail}</small><em>{count}</em></span>
            <ArrowRight size={19} />
          </button>
        ))}
      </div>

      <section className="flow-panel" aria-labelledby="source-heading">
        <div className="flow-panel-head"><div><h2 id="source-heading">Project material</h2><p>Add Markdown, text, code, PDF, or Word files. Athena analyzes each file as soon as it is added.</p></div><button className="primary-action" type="button" onClick={() => fileRef.current?.click()} disabled={!!busy}>{busy === 'upload' ? <LoaderCircle className="spin" size={17} /> : <Paperclip size={17} />} Add files</button></div>
        <input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md,.sql,.py,.js,.jsx,.ts,.tsx,.json,.yaml,.yml" multiple hidden onChange={onUpload} />
        {uploadReport && <div className={`flow-upload-result ${uploadReport.extractionFailures || uploadReport.failed?.length ? 'flow-upload-warning' : ''}`} role="status">
          <strong>{busy === 'upload' ? `Adding files: ${uploadReport.processed} of ${uploadReport.total} checked` : `${uploadReport.completed} of ${uploadReport.total} files added`}</strong>
          {uploadReport.completed > 0 && <span>{uploadReport.requirements} requirements found · {uploadReport.gaps} missing details noted.</span>}
          {uploadReport.analysisIssues?.map((issue, index) => <span key={`${issue.name}-${index}`}>{issue.name}: {issue.reason}</span>)}
          {uploadReport.failed?.map((failure, index) => <span key={`${failure.name}-${index}`}>{failure.name} could not be added: {failure.reason}</span>)}
          {uploadReport.requirements > 0 && <button type="button" onClick={() => onNavigate('evidence')}>View findings <ArrowRight size={16} /></button>}
        </div>}
        {sourceFiles.length ? <div className="flow-source-list">{sourceFiles.map((source) => <div className="flow-source" key={source.id}><FileCheck2 size={17} /><span title={source.ref}>{source.ref}</span><small>{failedSourceIds.includes(source.id) ? 'Saved · analysis unavailable' : `Added ${new Date(source.captured_at).toLocaleDateString()}`}</small></div>)}</div> : <p className="flow-empty">No files yet. Add a project brief, meeting notes, or requirements file to begin.</p>}
        {failedSourceIds.length > 0 && <p className="flow-source-help">A saved file could not be analyzed. You can still record its key details under “Work with Athena.”</p>}
      </section>

      <section className="flow-panel" aria-labelledby="analyst-heading">
        <div className="flow-panel-head"><div><h2 id="analyst-heading">Work with Athena</h2><p>Record a confirmed detail, or ask a question about the material already in this project.</p></div></div>
        <div className="flow-mode-switch" role="group" aria-label="Choose how to work with Athena">
          <button type="button" className={analysisMode === 'record' ? 'selected' : ''} aria-pressed={analysisMode === 'record'} onClick={() => onModeChange('record')}>Record confirmed information</button>
          <button type="button" className={analysisMode === 'investigate' ? 'selected' : ''} aria-pressed={analysisMode === 'investigate'} onClick={() => onModeChange('investigate')}>Ask about this project</button>
        </div>
        <form className="flow-prompt" onSubmit={onSubmitPrompt}>
          <label htmlFor="analysis-prompt">{analysisMode === 'record' ? 'What did your team confirm?' : 'What do you want to know?'}</label>
          <textarea id="analysis-prompt" rows="3" value={prompt} onChange={(event) => onPromptChange(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) onSubmitPrompt(event) }} placeholder={analysisMode === 'record' ? 'For example: The operations lead approves stock adjustments.' : 'For example: Which requirements still need evidence?'} />
          <button className="primary-action" type="submit" disabled={!prompt.trim() || !!busy}>{busy === 'analysis' ? <LoaderCircle className="spin" size={17} /> : analysisMode === 'record' ? <Check size={17} /> : <Search size={17} />}{analysisMode === 'record' ? 'Record information' : 'Ask Athena'}</button>
        </form>
      </section>

      {feedback && <p className="flow-feedback" role="status">{feedback}</p>}
      {activity.length > 0 && <section className="flow-panel" aria-label="Recent activity"><h2>Latest results</h2>{activity.slice(0, 3).map((item) => <article className="flow-activity" key={item.id}><strong>{item.file ? `Added ${item.file}` : item.clarification ? 'Question answered' : item.investigating ? item.message : 'Information recorded'}</strong>{item.investigating && <p>{item.result?.answer}</p>}{!item.investigating && item.result && <p>{item.result.facts_created || 0} findings recorded. <button type="button" onClick={() => onNavigate('evidence')}>View findings</button></p>}{item.response && <p>{item.response}</p>}{item.conflict && <p className="flow-conflict" role="alert">{item.conflict}</p>}</article>)}</section>}
    </section>
  )
}

const scopeSections = [
  ['Project narrative', [['Vision', 'vision'], ['Mission', 'mission'], ['Problem', 'problem_statement'], ['Purpose', 'project_purpose'], ['Target users', 'icp'], ['Functional overview', 'functional_scope']]],
  ['Business outcomes', [['Goals', 'business_goals'], ['Success measures', 'success_measures'], ['Stakeholders', 'stakeholders']]],
  ['Prioritized scope', [['Must have', 'must_have_features'], ['Should have', 'should_have_features'], ['Could have', 'could_have_features'], ["Won’t have", 'wont_have_features'], ['In scope', 'in_scope_features'], ['Out of scope', 'out_of_scope_features'], ['Future enhancements', 'future_enhancements']]],
  ['Delivery context', [['Business rules', 'key_business_rules'], ['Constraints', 'constraints'], ['Dependencies', 'dependencies'], ['Risks', 'risks'], ['Assumptions', 'important_assumptions'], ['Open decisions', 'open_decisions'], ['Other context', 'other_context']]],
]

export function FindingsView({ project, findings, sources, busy, onNavigate, onDownloadSource, onApproveScope, questionsComplete }) {
  const sourceById = Object.fromEntries(sources.map((source) => [source.id, source]))
  const summary = project.settings?.project_summary || {}
  const businessContext = summary.business_context || {}
  const contextHasData = Object.values(businessContext).some((section) => section && Object.values(section).some((value) => value != null && value !== '' && (!Array.isArray(value) || value.length)))
  const hasSummary = contextHasData || Object.values(summary).some((value) => typeof value === 'string' ? value.trim() : Array.isArray(value) && value.length)
  return <section className="flow-page flow-detail">
    <div className="flow-heading"><h1>Project scope</h1><p>A consistent business analysis view of what the project is, what belongs in it, and what still needs a decision. Tell Athena in the chat when something should change.</p></div>
    <div className="scope-approval"><span className={project.settings?.scope_approved ? 'flow-status confirmed' : 'flow-status'}>{project.settings?.scope_approved ? 'Approved' : 'Draft for review'}</span>{hasSummary && !project.settings?.scope_approved && <button type="button" onClick={onApproveScope} disabled={!!busy || !questionsComplete} title={!questionsComplete ? 'Answer the open questions in chat first' : undefined}><Check size={16} /> Approve scope</button>}<button type="button" onClick={() => onNavigate('workspace')}>Discuss a change <ArrowRight size={16} /></button></div>
    {scopeSections.map(([title, fields]) => <section className="scope-section" key={title}><h2>{title}</h2><div className="scope-grid">{fields.map(([label, key]) => { const value = summary[key] || (key === 'must_have_features' ? project.settings?.must_have : key === 'should_have_features' ? project.settings?.should_have : null); const entries = Array.isArray(value) ? value.filter(Boolean) : typeof value === 'string' && value.trim() ? [value] : []; return <div className="scope-field" key={key}><h3>{label}</h3>{entries.length ? entries.length === 1 ? <p>{entries[0]}</p> : <ul>{entries.map((entry, index) => <li key={index}>{entry}</li>)}</ul> : <p className="scope-missing">Needs confirmation</p>}</div> })}</div></section>)}
    <section className="business-context-section"><h2>Business Context Object</h2><p>Evidence-backed context for the company, its people, processes, products, systems, data, and goals. Missing values stay visible so Athena can clarify them in chat.</p><div className="business-context-grid">{Object.entries(businessContext).map(([section, fields]) => {
      const entries = contextEntries(fields)
      const populated = entries.filter(([, value]) => value != null)
      return <details className="business-context-card" key={section}><summary><span>{humanize(section)}</span><small>{Array.isArray(fields) ? `${entries.length} recorded` : `${populated.length} of ${entries.length} established`}</small></summary><dl>{entries.length ? entries.map(([label, value], index) => <div key={index}><dt>{label}</dt><dd>{value == null ? <span className="scope-missing">Needs confirmation</span> : value || 'No further details yet'}</dd></div>) : <div><dt>None recorded yet</dt><dd><span className="scope-missing">Needs confirmation</span></dd></div>}</dl></details>
    })}</div></section>
    <h2 className="scope-evidence-heading">Supporting findings and sources</h2>
    <div className="flow-summary"><strong>{findings.length} {findings.length === 1 ? 'finding' : 'findings'}</strong><span>Recorded in project</span></div>
    {findings.length ? <div className="flow-finding-list">{[...findings].reverse().map((fact) => <article className="flow-finding" key={fact.id}>
      <div className="flow-finding-top"><strong>{fact.subject_type.replaceAll('_', ' ')}</strong><span className={fact.human_approval ? 'flow-status confirmed' : 'flow-status'}>{fact.human_approval ? 'Confirmed' : 'Recorded'}</span></div>
      <p>{fact.display}</p>
      {sourceById[fact.source_id] && <small>From {sourceById[fact.source_id].ref}</small>}
      {fact.citations?.length > 0 && <div className="flow-citations" aria-label="Cited source excerpts">
        <strong>Cited text from {sourceById[fact.source_id]?.ref || 'project source'}</strong>
        {fact.citations.map((span) => <blockquote key={span.id}>{span.text}</blockquote>)}
      </div>}
      {sourceById[fact.source_id]?.kind === 'document' && <button type="button" onClick={() => onDownloadSource(sourceById[fact.source_id])} disabled={!!busy}><Download size={16} /> Inspect source</button>}
    </article>)}</div> : <div className="flow-empty-state"><FilePlus2 size={30} /><h2>No findings yet</h2><p>Add a source or record a confirmed project detail. Findings will appear here as material is analyzed.</p><button type="button" onClick={() => onNavigate('workspace')}>Go to project material <ArrowRight size={16} /></button></div>}
    {findings.length > 0 && <button className="flow-next" type="button" onClick={() => onNavigate('workspace')}>Continue with Athena <ArrowRight size={17} /></button>}
  </section>
}

function PdfPreview({ document, token, projectId }) {
  const [preview, setPreview] = useState({ url: '', error: '' })
  useEffect(() => {
    const controller = new AbortController()
    let url = ''
    setPreview({ url: '', error: '' })
    async function load() {
      try {
        const response = await fetch(apiUrl(`/api/ba/projects/${projectId}/deliverables/${document.id}/download`), { headers: { Authorization: `Bearer ${token}` }, signal: controller.signal })
        if (!response.ok) throw new Error('The PDF could not be opened. Try downloading it.')
        const blob = await response.blob()
        if (controller.signal.aborted) return
        url = URL.createObjectURL(blob)
        setPreview({ url, error: '' })
      } catch (error) {
        if (!controller.signal.aborted) setPreview({ url: '', error: error.message })
      }
    }
    load()
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url) }
  }, [document.id, token, projectId])
  if (preview.error) return <p role="alert">{preview.error}</p>
  return preview.url ? <iframe className="document-pdf-preview" title={`${document.name} PDF`} src={preview.url} /> : <p role="status">Opening PDF...</p>
}

export function DocumentsView({ token, projectId, catalog, documentKey, onKeyChange, documents, openDocument, busy, findingsCount, onGenerate, onView, onDownload, onNavigate }) {
  const selected = catalog.find((item) => item.key === documentKey)
  const startingTypes = catalog.filter((item) => ['brd', 'user_story', 'frd'].includes(item.key))
  const otherTypes = catalog.filter((item) => !['brd', 'user_story', 'frd'].includes(item.key))
  return <section className="flow-page flow-detail">
    <div className="flow-heading"><span className="flow-eyebrow">Step 3 of 3</span><h1>Create a document</h1><p>Choose a document, create a draft from the current project record, then read it before downloading or sharing.</p></div>
    <div className="flow-panel flow-document-create">
      <label htmlFor="document-kind">Document type</label>
      <select id="document-kind" value={documentKey} onChange={(event) => onKeyChange(event.target.value)} disabled={!catalog.length || !!busy}>
        {startingTypes.length > 0 && <optgroup label="Good starting points">{startingTypes.map((item) => <option key={item.key} value={item.key}>{item.name}</option>)}</optgroup>}
        {otherTypes.length > 0 && <optgroup label="Other document types">{otherTypes.map((item) => <option key={item.key} value={item.key}>{item.name}</option>)}</optgroup>}
      </select>
      {selected && <p>{selected.purpose}</p>}
      {!catalog.length && <p>Document types are unavailable. Check that the deliverable catalog has been seeded.</p>}
      {!findingsCount && <p>Add material first so the draft has project evidence to use.</p>}
      <button className="primary-action" type="button" onClick={() => onGenerate()} disabled={!catalog.length || !findingsCount || !!busy}>{busy === 'generate' ? <LoaderCircle className="spin" size={17} /> : <FileCheck2 size={17} />} Create draft</button>
    </div>
    <div className="flow-panel"><h2>Saved drafts</h2>{documents.length ? <div className="flow-document-list">{[...documents].reverse().map((item) => <article className="flow-document" key={item.id}><div><strong>{item.name}</strong><small>{item.is_stale ? 'Project changed since this draft' : item.status === 'approved' ? 'Approved' : 'Draft'} · {deliverableFormat(item.output_format)}</small>{item.is_stale && <p className="flow-stale">Create a fresh draft above to include the latest project findings.</p>}</div><div className="flow-document-actions"><button type="button" onClick={() => onView(item)} disabled={!!busy}><Eye size={16} /> Read</button><button type="button" onClick={() => onDownload(item)} disabled={!!busy}><Download size={16} /> {item.is_stale ? 'Download old draft' : 'Download'}</button></div></article>)}</div> : <p className="flow-empty">No drafts yet. Choose a document type above to create the first one.</p>}</div>
    {openDocument && <div className="flow-panel flow-preview"><div className="flow-panel-head"><div><h2>{openDocument.name}</h2><p>{deliverableFormat(openDocument.output_format)} preview</p></div><button type="button" onClick={() => onDownload(openDocument)} disabled={!!busy}><Download size={16} /> Download</button></div>{openDocument.output_format?.toLowerCase() === 'pdf' ? <PdfPreview document={openDocument} token={token} projectId={projectId} /> : <pre>{openDocument.content}</pre>}</div>}
    {!findingsCount && <button className="flow-next" type="button" onClick={() => onNavigate('workspace')}>Start with project material <ArrowRight size={17} /></button>}
  </section>
}
