import { useEffect, useRef, useState } from 'react'
import { DocumentsView, FindingsView } from './ProjectViews'
import { ProjectConversation } from './ProjectConversation'
import {
  ArrowRight, ArrowUpRight, BookOpen, ChevronDown, CircleHelp, FileCheck2,
  FolderOpen, KeyRound, LayoutDashboard, LoaderCircle, Menu, Plus, RotateCcw, ShieldCheck, Trash2, X,
} from 'lucide-react'

const navigation = [
  { id: 'workspace', label: 'Chat', icon: LayoutDashboard },
  { id: 'evidence', label: 'Findings', icon: BookOpen },
  { id: 'deliverables', label: 'Deliverables', icon: FileCheck2 },
]

function getErrorMessage(data, fallback) {
  if (typeof data?.detail === 'string') return data.detail
  if (Array.isArray(data?.detail)) return data.detail.map((item) => item.msg).join(', ')
  return fallback
}

async function request(path, token, options = {}) {
  const headers = { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers }
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json'
  const response = await fetch(path, { ...options, headers })
  const data = await response.json().catch(() => null)
  if (!response.ok) throw new Error(getErrorMessage(data, `Request failed (${response.status}).`))
  return data
}

async function requestAudio(path, token, payload) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    throw new Error(getErrorMessage(data, `Request failed (${response.status}).`))
  }
  return response.blob()
}

async function streamProjectAction(path, token, payload, onEvent) {
  const isForm = payload instanceof FormData
  const response = await fetch(path, {
    method: 'POST', headers: { Authorization: `Bearer ${token}`, ...(isForm ? {} : { 'Content-Type': 'application/json' }) },
    body: isForm ? payload : JSON.stringify(payload),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    throw new Error(getErrorMessage(data, `Request failed (${response.status}).`))
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let answer = ''
  let result = null
  let completed = false
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() || ''
    for (const frame of frames) {
      const line = frame.split('\n').find((part) => part.startsWith('data: '))
      if (!line) continue
      const event = JSON.parse(line.slice(6))
      onEvent(event)
      if (event.type === 'answer') { answer = event.text; result = { ...(result || {}), answer } }
      if (event.type === 'evaluation') result = { ...(result || {}), evaluation: event.data }
      if (event.type === 'result') result = { ...(result || {}), ...event.data }
      if (event.type === 'done') completed = true
      if (event.type === 'error') throw new Error(event.message)
    }
  }
  if (!completed) throw new Error('The analysis stream ended before Athena finished. Please try again.')
  return result || { answer }
}

function hasUnderstanding(summary) {
  return !!summary && Object.entries(summary).some(([key, value]) => key === 'business_context'
    ? Object.values(value || {}).some((section) => Object.values(section || {}).some((item) => item != null && item !== '' && (!Array.isArray(item) || item.length)))
    : typeof value === 'string' ? value.trim() : Array.isArray(value) && value.length)
}

// The server accepts at most this many files per streamed upload.
const UPLOAD_BATCH_SIZE = 20

function describeFact(fact) {
  const value = fact.value
  if (typeof value === 'string') return value
  if (!value || typeof value !== 'object') return 'Recorded without a display value'
  if (value.task) return [value.stakeholder ? `For ${value.stakeholder}` : null, value.task, value.object, value.benefit ? `so that ${value.benefit}` : null].filter(Boolean).join(' ')
  if (fact.subject_type === 'Requirement') return value.object ? `Requirement about ${value.object}. Some details still need an answer.` : 'A requirement was identified, but its action is not clear yet. Answer an open question to complete it.'
  if (value.reason) return value.reason
  return value.description || value.answer || value.name || value.question || value.text ||
    (Array.isArray(value.items) ? value.items.join(' Â· ') : Object.entries(value).filter(([, item]) => typeof item === 'string').slice(0, 2).map(([key, item]) => `${key.replaceAll('_', ' ')}: ${item}`).join(' Â· ') || 'Recorded detail')
}

function documentName(key) {
  return ({ brd: 'Business requirements document', frd: 'Functional requirements document', nfr_spec: 'Non-functional requirements', rtm: 'Requirements traceability matrix' })[key] || key.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function AthenaMark({ className = '' }) {
  return <span className={`athena-mark ${className}`} aria-hidden="true" />
}

function Modal({ title, description, onClose, children }) {
  const dialogRef = useRef(null)
  useEffect(() => {
    const previous = document.activeElement
    dialogRef.current?.querySelector('[autofocus], button, input, textarea, select')?.focus()
    function onKeyDown(event) {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return }
      if (event.key !== 'Tab') return
      const controls = [...dialogRef.current.querySelectorAll('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])')]
      if (!controls.length) return
      const first = controls[0], last = controls[controls.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => { document.removeEventListener('keydown', onKeyDown); previous?.focus?.() }
  }, [])
  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section ref={dialogRef} className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <button className="icon-button modal-close" type="button" onClick={onClose} aria-label="Close dialog"><X size={18} /></button>
        <div className="modal-icon"><AthenaMark /></div>
        <h2>{title}</h2>
        <p>{description}</p>
        {children}
      </section>
    </div>
  )
}

export default function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem('athena_token') || '')
  const [user, setUser] = useState(null)
  const [authMode, setAuthMode] = useState('login')
  const [authName, setAuthName] = useState('')
  const [authEmail, setAuthEmail] = useState('')
  const [authPassword, setAuthPassword] = useState('')
  const [authError, setAuthError] = useState('')
  const [projects, setProjects] = useState([])
  const [projectsLoaded, setProjectsLoaded] = useState(false)
  const [activeId, setActiveId] = useState(() => sessionStorage.getItem('athena_project') || '')
  const [facts, setFacts] = useState([])
  const [sources, setSources] = useState([])
  const [deliverables, setDeliverables] = useState([])
  const [catalog, setCatalog] = useState([])
  const [documentKey, setDocumentKey] = useState('brd')
  const [openDocument, setOpenDocument] = useState(null)
  const [uploadReport, setUploadReport] = useState(null)
  const [feedback, setFeedback] = useState('')
  const [conflictNotice, setConflictNotice] = useState('')
  const [view, setView] = useState('workspace')
  const [prompt, setPrompt] = useState('')
  const [projectName, setProjectName] = useState('')
  const [activity, setActivity] = useState([])
  const [analysisSteps, setAnalysisSteps] = useState([])
  const [liveAnswer, setLiveAnswer] = useState('')
  const [liveThinking, setLiveThinking] = useState('')
  const [retryProgress, setRetryProgress] = useState('')
  const [clarification, setClarification] = useState(null)
  const [questionsComplete, setQuestionsComplete] = useState(false)
  const [modal, setModal] = useState(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [projectToDelete, setProjectToDelete] = useState(null)
  const [deleteError, setDeleteError] = useState('')
  const fileRef = useRef(null)
  const scopeRef = useRef({ token, projectId: activeId, version: 0 })
  const loadSeqRef = useRef({ projects: 0, facts: 0, sources: 0, deliverables: 0, catalog: 0 })
  const questionSeqRef = useRef(0)
  const isCurrent = (scope) => scopeRef.current === scope
  function changeProject(projectId) {
    if (scopeRef.current.projectId !== projectId) {
      scopeRef.current = { ...scopeRef.current, projectId, version: scopeRef.current.version + 1 }
    }
    setActiveId(projectId)
  }
  function changeToken(nextToken) {
    scopeRef.current = { token: nextToken, projectId: '', version: scopeRef.current.version + 1 }
    setToken(nextToken)
  }

  const activeProject = projects.find((project) => project.id === activeId)

  async function loadProjects(authToken = token) {
    const scope = scopeRef.current
    const sequence = ++loadSeqRef.current.projects
    try {
      const items = await request('/api/ba/projects', authToken)
      if (!isCurrent(scope) || scope.token !== authToken || sequence !== loadSeqRef.current.projects) return
      setProjects(items)
      setProjectsLoaded(true)
      const remembered = sessionStorage.getItem('athena_project')
      const nextId = items.some((item) => item.id === remembered) ? remembered : items[0]?.id || ''
      changeProject(nextId)
      if (nextId) sessionStorage.setItem('athena_project', nextId)
      else sessionStorage.removeItem('athena_project')
      setError('')
    } catch (cause) {
      if (!isCurrent(scope) || sequence !== loadSeqRef.current.projects) return
      setProjectsLoaded(true)
      setError(cause.message)
    }
  }

  async function loadFacts(projectId = activeId) {
    if (!token || !projectId) return
    const scope = scopeRef.current
    if (scope.projectId !== projectId || scope.token !== token) return
    const sequence = ++loadSeqRef.current.facts
    try {
      const items = await request(`/api/ba/projects/${projectId}/facts`, token)
      if (isCurrent(scope) && sequence === loadSeqRef.current.facts) {
        setFacts(items)
        const questions = new Map(items.filter((fact) => fact.subject_type === 'ClarificationQuestion' && fact.predicate === 'asked').map((fact) => [fact.subject_key, fact.value?.question]))
        setActivity(items.filter((fact) => fact.subject_type === 'ConversationTurn' || (fact.subject_type === 'ClarificationQuestion' && fact.predicate === 'answered'))
          .sort((a, b) => a.seq - b.seq)
          .reverse()
          .map((fact) => fact.subject_type === 'ConversationTurn'
            ? fact.predicate === 'deliverable'
              ? { id: fact.id, document: documentName(fact.value?.deliverable_key || 'draft'), documentId: fact.value?.instance_id }
              : fact.predicate === 'upload'
                ? { id: fact.id, file: fact.value?.filename }
              : { id: fact.id, message: fact.predicate === 'message' ? fact.value?.text : fact.value?.question, investigating: fact.predicate === 'investigation', result: fact.predicate === 'investigation' ? { answer: fact.value?.answer, evaluation: fact.value?.evaluation } : { facts_created: fact.value?.facts_created, reply: fact.value?.reply }, steps: (fact.value?.steps || []).map((tool) => ({ type: 'tool_call', tool })) }
            : { id: fact.id, clarification: questions.get(fact.subject_key), response: fact.value?.answer }))
      }
    } catch (cause) {
      if (isCurrent(scope) && sequence === loadSeqRef.current.facts) setError(cause.message)
    }
  }

  async function loadSources(projectId = activeId) {
    if (!token || !projectId) return
    const scope = scopeRef.current
    if (scope.projectId !== projectId || scope.token !== token) return
    const sequence = ++loadSeqRef.current.sources
    try {
      const items = await request(`/api/ba/projects/${projectId}/sources`, token)
      if (isCurrent(scope) && sequence === loadSeqRef.current.sources) setSources(items)
    } catch (cause) {
      if (isCurrent(scope) && sequence === loadSeqRef.current.sources) setError(cause.message)
    }
  }

  async function loadDeliverables(projectId = activeId) {
    if (!token || !projectId) return
    const scope = scopeRef.current
    if (scope.projectId !== projectId || scope.token !== token) return
    const sequence = ++loadSeqRef.current.deliverables
    try {
      const items = await request(`/api/ba/projects/${projectId}/deliverables`, token)
      if (isCurrent(scope) && sequence === loadSeqRef.current.deliverables) setDeliverables(items)
    } catch (cause) {
      if (isCurrent(scope) && sequence === loadSeqRef.current.deliverables) setError(cause.message)
    }
  }

  async function loadCatalog(projectId = activeId) {
    if (!token || !projectId) return
    const scope = scopeRef.current
    if (scope.projectId !== projectId || scope.token !== token) return
    const sequence = ++loadSeqRef.current.catalog
    try {
      const items = await request(`/api/ba/projects/${projectId}/deliverables/catalog`, token)
      if (isCurrent(scope) && sequence === loadSeqRef.current.catalog) {
        setCatalog(items)
        if (items.length && !items.some((item) => item.key === documentKey)) setDocumentKey(items[0].key)
      }
    } catch (cause) {
      if (isCurrent(scope) && sequence === loadSeqRef.current.catalog) setError(cause.message)
    }
  }

  useEffect(() => {
    if (!token) return
    const scope = scopeRef.current
    let cancelled = false
    request('/api/auth/me', token).then((account) => {
      if (!cancelled && isCurrent(scope)) { setUser(account); loadProjects(token) }
    }).catch(() => { if (!cancelled && isCurrent(scope)) disconnect() })
    return () => { cancelled = true }
  }, [token])

  useEffect(() => {
    if (scopeRef.current.projectId !== activeId || scopeRef.current.token !== token) {
      scopeRef.current = { token, projectId: activeId, version: scopeRef.current.version + 1 }
    }
    setBusy('')
    setError('')
    setFacts([])
    setSources([])
    setDeliverables([])
    setCatalog([])
    setOpenDocument(null)
    setUploadReport(null)
    setFeedback('')
    setConflictNotice('')
    setClarification(null)
    setQuestionsComplete(false)
    setActivity([])
    setAnalysisSteps([])
    if (activeId && token) {
      loadFacts(activeId)
      loadSources(activeId)
      loadDeliverables(activeId)
      loadCatalog(activeId)
    }
  }, [activeId, token])

  async function connect(event) {
    event.preventDefault()
    const scope = scopeRef.current
    setBusy('auth')
    setAuthError('')
    try {
      const body = authMode === 'register'
        ? { name: authName.trim(), email: authEmail.trim(), password: authPassword }
        : { email: authEmail.trim(), password: authPassword }
      const result = await request(`/api/auth/${authMode}`, '', { method: 'POST', body: JSON.stringify(body) })
      if (!isCurrent(scope)) return
      sessionStorage.setItem('athena_token', result.access_token)
      setUser(result.user)
      changeToken(result.access_token)
      setAuthPassword('')
      setModal(null)
      setError('')
    } catch (cause) {
      if (isCurrent(scope)) setAuthError(cause.message)
    } finally {
      if (isCurrent(scope)) setBusy('')
    }
  }

  function disconnect() {
    sessionStorage.removeItem('athena_token')
    sessionStorage.removeItem('athena_project')
    changeToken('')
    setUser(null)
    setProjects([])
    setProjectsLoaded(false)
    changeProject('')
    setFacts([])
    setSources([])
    setDeliverables([])
    setCatalog([])
    setOpenDocument(null)
    setUploadReport(null)
    setFeedback('')
    setConflictNotice('')
    setActivity([])
    setView('workspace')
    setModal(null)
    setProjectToDelete(null)
    setDeleteError('')
  }

  async function createProject(event) {
    event.preventDefault()
    if (!token) { setModal('connect'); return }
    if (!projectName.trim()) return
    const scope = scopeRef.current
    setBusy('project')
    setError('')
    try {
      const project = await request('/api/ba/projects', token, {
        method: 'POST', body: JSON.stringify({ name: projectName.trim() }),
      })
      if (!isCurrent(scope)) return
      setProjects((current) => [project, ...current])
      changeProject(project.id)
      sessionStorage.setItem('athena_project', project.id)
      setProjectName('')
      setModal(null)
      setView('workspace')
    } catch (cause) {
      if (isCurrent(scope)) setError(cause.message)
    } finally {
      if (isCurrent(scope)) setBusy('')
    }
  }
  async function deleteProject(projectId) {
    if (!token || !projectId) return
    const scope = scopeRef.current
    const tokenAtStart = token
    setBusy('delete-project')
    setDeleteError('')
    try {
      const response = await fetch(`/api/ba/projects/${encodeURIComponent(projectId)}`, {
        method: 'DELETE',
        headers: {
          Authorization: `Bearer ${tokenAtStart}`,
        },
      })
      if (response.status !== 204) {
        const data = await response.json().catch(() => null)
        throw new Error(getErrorMessage(data, response.status === 404 ? 'Project not found or already deleted.' : `Failed to delete project (${response.status}).`))
      }
      if (!isCurrent(scope) || scopeRef.current.token !== tokenAtStart) return

      const remaining = projects.filter((project) => project.id !== projectId)
      setProjects(remaining)
      setProjectToDelete(null)
      setDeleteError('')

      if (projectId === activeId) {
        const nextId = remaining[0]?.id || ''
        changeProject(nextId)
        if (nextId) {
          sessionStorage.setItem('athena_project', nextId)
        } else {
          sessionStorage.removeItem('athena_project')
        }
        setView('workspace')
      }
      setFeedback('Project deleted.')
      setModal(null)
    } catch (cause) {
      if (isCurrent(scope) && scopeRef.current.token === tokenAtStart) {
        setDeleteError(cause.message)
      }
    } finally {
      if (isCurrent(scope) && scopeRef.current.token === tokenAtStart) {
        setBusy('')
      }
    }
  }


  async function submitPrompt(event) {
    event.preventDefault()
    if (!prompt.trim() || busy) return
    if (!token) { setModal('connect'); return }
    if (!activeId) { setModal('project'); return }
    const projectId = activeId
    const scope = scopeRef.current
    const message = prompt.trim()
    const turnId = crypto.randomUUID()
    setActivity((current) => [{ id: turnId, message, pending: true }, ...current])
    setPrompt('')
    setBusy('analysis')
    setError('')
    const routingStep = { type: 'tool_call', tool: 'classify_chat_action', specialist: 'Athena' }
    setAnalysisSteps([routingStep])
    try {
      const intent = await request(`/api/ba/projects/${projectId}/chat/intent`, token, {
        method: 'POST', body: JSON.stringify({ message, pending_question: clarification?.question || null }),
      })
      if (!isCurrent(scope)) return
      if (clarification && intent.action === 'answer') {
        setAnalysisSteps([])
        await submitAnswer(event, message, turnId)
        return
      }
      if (intent.action === 'deliverable' && intent.deliverable_key) {
        setAnalysisSteps([])
        await generateDocument(intent.deliverable_key, message, turnId)
        return
      }
      const investigating = intent.action === 'investigate'
      const observedSteps = [routingStep]
      setAnalysisSteps(observedSteps)
      setLiveAnswer('')
      setLiveThinking('')
      const result = await streamProjectAction(
        `/api/ba/projects/${projectId}/${investigating ? 'analyze' : 'chat'}/stream`, token,
        investigating ? { question: message } : { message }, (event) => {
          if (!isCurrent(scope)) return
          if (['status', 'tool_call', 'tool_result'].includes(event.type)) {
            observedSteps.push(event)
            setAnalysisSteps([...observedSteps])
            // Text streamed before a tool call was a preamble, not the answer.
            if (event.type === 'tool_call') setLiveAnswer('')
          }
          if (event.type === 'thinking_delta') setLiveThinking((text) => text + event.text)
          if (event.type === 'answer_delta') setLiveAnswer((text) => text + event.text)
        },
      )
      if (!isCurrent(scope)) return
      setActivity((current) => current.map((item) => item.id === turnId
        ? { ...item, pending: false, result, investigating, steps: observedSteps }
        : item))
      setAnalysisSteps([])
      setLiveAnswer('')
      setLiveThinking('')
      if (result.project_summary) setProjects((current) => current.map((project) => project.id === projectId ? { ...project, settings: { ...project.settings, project_summary: result.project_summary } } : project))
      setFeedback(investigating ? 'Investigation complete. Read the result below.' : 'Context recorded and added to the project record.')
      if (!investigating) {
        await loadFacts(projectId)
        await nextClarification(projectId)
      }
    } catch (cause) {
      if (isCurrent(scope)) {
        setError(cause.message)
        setActivity((current) => current.filter((item) => item.id !== turnId))
        setPrompt(message)
        setAnalysisSteps([])
        setLiveAnswer('')
        setLiveThinking('')
      }
    } finally {
      if (isCurrent(scope)) setBusy('')
    }
  }

  async function uploadFile(event) {
    const files = [...(event.target.files || [])].filter((file) => /\.(pdf|docx|txt|md|sql|py|js|jsx|ts|tsx|json|yaml|yml)$/i.test(file.name))
    event.target.value = ''
    if (!files.length) { setError('No supported source files were selected.'); return }
    if (!token) { setModal('connect'); return }
    if (!activeId) { setModal('project'); return }
    const projectId = activeId
    const scope = scopeRef.current
    setBusy('upload')
    setError('')
    let completed = 0
    let requirements = 0
    let gaps = 0
    let contextFacts = 0
    let understanding = null
    let extractionFailures = 0
    const analysisIssues = []
    const failed = []
    const partFindings = []
    const observedSteps = []
    setUploadReport({ completed: 0, requirements: 0, gaps: 0, contextFacts: 0, extractionFailures: 0, analysisIssues: [], failed: [], partFindings: [], processed: 0, total: files.length })
    try {
      // Every file in a batch is analyzed concurrently on the server; progress streams per file and part.
      for (let start = 0; start < files.length; start += UPLOAD_BATCH_SIZE) {
        if (!isCurrent(scope)) break
        const batch = files.slice(start, start + UPLOAD_BATCH_SIZE)
        const body = new FormData()
        batch.forEach((file) => body.append('files', file, file.webkitRelativePath || file.name))
        try {
          const result = await streamProjectAction(`/api/ba/projects/${projectId}/sources/stream`, token, body, (event) => {
            if (!isCurrent(scope) || !['status', 'tool_call', 'tool_result'].includes(event.type)) return
            observedSteps.push(event)
            setAnalysisSteps([...observedSteps])
            if (event.type === 'tool_result' && event.tool === 'analyze_source') {
              partFindings.push(event.message)
              setUploadReport((report) => report && { ...report, partFindings: [...partFindings] })
            }
          })
          for (const entry of result.sources || []) {
            if (entry.error) { failed.push({ name: entry.ref, reason: entry.error }); continue }
            completed += 1
            requirements += entry.extraction?.requirements_created || 0
            gaps += entry.extraction?.gaps_created || 0
            contextFacts += entry.extraction?.facts_created || 0
            if (entry.duplicate) analysisIssues.push({ name: entry.ref, reason: 'This file is already in the project, so it was not analyzed again.' })
            if (entry.extraction_issue) {
              extractionFailures += 1
              analysisIssues.push({ name: entry.ref, reason: entry.extraction_issue })
            }
          }
          if (hasUnderstanding(result.project_summary)) understanding = result.project_summary
        } catch (cause) {
          batch.forEach((file) => failed.push({ name: file.name, reason: cause.message }))
        }
        if (isCurrent(scope)) {
          setUploadReport({ completed, requirements, gaps, contextFacts, extractionFailures, understanding, analysisIssues: [...analysisIssues], failed: [...failed], partFindings: [...partFindings], processed: completed + failed.length, total: files.length })
        }
      }
    } finally {
      if (isCurrent(scope)) setAnalysisSteps([])
      if (isCurrent(scope)) {
        await Promise.allSettled([loadFacts(projectId), loadSources(projectId)])
        if (completed) await loadProjects(token)
        if (isCurrent(scope)) setBusy('')
        if (completed && isCurrent(scope)) {
          await new Promise((resolve) => window.requestAnimationFrame(resolve))
          await nextClarification(projectId)
        }
      }
    }
  }

  async function retryFailedSources(ids = failedSourceIds) {
    if (!token || !activeId || !ids.length || busy) return
    const projectId = activeId
    const scope = scopeRef.current
    setBusy('retry')
    setError('')
    let recovered = 0
    const stillFailing = []
    try {
      for (const [index, sourceId] of ids.entries()) {
        if (!isCurrent(scope)) break
        const name = sources.find((source) => source.id === sourceId)?.ref || 'file'
        setRetryProgress(`Analyzing ${name} (${index + 1} of ${ids.length})`)
        try {
          const result = await request(`/api/ba/projects/${projectId}/sources/${sourceId}/analyze`, token, { method: 'POST' })
          if (result.extraction_issue) stillFailing.push(`${name}: ${result.extraction_issue}`)
          else recovered += 1
        } catch (cause) {
          stillFailing.push(`${name}: ${cause.message}`)
        }
      }
      if (isCurrent(scope)) {
        setFeedback(`${recovered} of ${ids.length} ${ids.length === 1 ? 'file' : 'files'} fully analyzed.`)
        if (stillFailing.length) setError(stillFailing.join(' '))
      }
    } finally {
      if (isCurrent(scope)) {
        setRetryProgress('')
        await Promise.allSettled([loadFacts(projectId), loadSources(projectId), loadProjects(token)])
        setBusy('')
      }
    }
  }

  async function nextClarification(projectId = activeId) {
    if (!token) { setModal('connect'); return }
    if (!projectId) return
    const scope = scopeRef.current
    const sequence = ++questionSeqRef.current
    try {
      const result = await request(`/api/ba/projects/${projectId}/clarifications/next`, token)
      if (!isCurrent(scope) || sequence !== questionSeqRef.current) return
      setClarification(result?.question ? result : null)
      setQuestionsComplete(!result?.question)
    } catch (cause) {
      if (isCurrent(scope) && sequence === questionSeqRef.current) setError(cause.message)
    }
  }

  async function submitAnswer(event, suppliedAnswer, pendingTurnId = null) {
    event.preventDefault()
    if (!clarification || !suppliedAnswer) return
    const projectId = activeId
    const scope = scopeRef.current
    setBusy('answer')
    setError('')
    setAnalysisSteps([{ type: 'tool_call', tool: 'record_clarification_answer', specialist: 'Scope analyst' }])
    try {
      const result = await request(`/api/ba/projects/${projectId}/clarifications/${clarification.gap_key}/answer`, token, {
        method: 'POST', body: JSON.stringify({ answer: suppliedAnswer }),
      })
      if (!isCurrent(scope)) return
      const answeredTurn = {
        id: pendingTurnId || crypto.randomUUID(),
        clarification: clarification.question,
        response: suppliedAnswer,
        conflict: result.conflict_notice,
        pending: false,
      }
      setActivity((current) => pendingTurnId
        ? current.map((item) => item.id === pendingTurnId ? { ...item, ...answeredTurn } : item)
        : [answeredTurn, ...current])
      setConflictNotice(result.conflict_notice || '')
      setFeedback(result.conflict_notice ? '' : 'Answer recorded. Your project findings have been updated.')
      setClarification(null)
      setPrompt('')
      if (result.next_question) {
        setClarification(result.next_question.question ? result.next_question : null)
        setQuestionsComplete(!result.next_question.question)
      }
      // The scope rewrite finishes on the server after this response; refresh in the background.
      Promise.allSettled([loadFacts(projectId), loadProjects(token)])
      if (!result.next_question) await nextClarification(projectId)
    } catch (cause) {
      if (isCurrent(scope)) {
        setError(cause.message)
        if (pendingTurnId) {
          setActivity((current) => current.filter((item) => item.id !== pendingTurnId))
          setPrompt(suppliedAnswer)
        }
      }
    } finally {
      if (isCurrent(scope)) { setBusy(''); setAnalysisSteps([]) }
    }
  }


  async function generateDocument(requestedKey = documentKey, requestMessage = '', pendingTurnId = null) {
    if (!requestedKey) return
    const projectId = activeId
    const scope = scopeRef.current
    setBusy('generate')
    setError('')
    try {
      const result = await request(`/api/ba/projects/${projectId}/deliverables/${encodeURIComponent(requestedKey)}/generate`, token, { method: 'POST' })
      if (!isCurrent(scope)) return
      setOpenDocument({ id: result.id, deliverable_key: result.deliverable_key, output_format: result.output_format, content: result.content })
      const documentTurn = {
        id: pendingTurnId || crypto.randomUUID(),
        message: requestMessage,
        document: documentName(result.deliverable_key),
        documentId: result.id,
        documentContent: result.output_format === 'pdf' ? null : result.content,
        pending: false,
      }
      setActivity((current) => pendingTurnId
        ? current.map((item) => item.id === pendingTurnId ? { ...item, ...documentTurn } : item)
        : [documentTurn, ...current])
      await loadDeliverables(projectId)
      if (!isCurrent(scope)) return
      setFeedback('Draft created. Read it below before sharing it.')
    } catch (cause) {
      if (isCurrent(scope)) {
        setError(cause.message)
        if (pendingTurnId) {
          setActivity((current) => current.filter((item) => item.id !== pendingTurnId))
          setPrompt(requestMessage)
        }
      }
    } finally {
      if (isCurrent(scope)) setBusy('')
    }
  }

  async function approveScope() {
    const projectId = activeId
    const scope = scopeRef.current
    setBusy('approve-scope')
    setError('')
    try {
      const updated = await request(`/api/ba/projects/${projectId}`, token, { method: 'PATCH', body: JSON.stringify({ scope_approved: true }) })
      if (isCurrent(scope)) setProjects((current) => current.map((item) => item.id === projectId ? { ...item, ...updated } : item))
    } catch (cause) {
      if (isCurrent(scope)) setError(cause.message)
    } finally {
      if (isCurrent(scope)) setBusy('')
    }
  }

  async function viewDocument(item) {
    const projectId = activeId
    const scope = scopeRef.current
    setBusy(`open-${item.id}`)
    setError('')
    try {
      const result = await request(`/api/ba/projects/${projectId}/deliverables/${item.id}`, token)
      if (isCurrent(scope)) setOpenDocument({ ...result, is_stale: result.is_stale ?? item.is_stale })
    } catch (cause) {
      if (isCurrent(scope)) setError(cause.message)
    } finally {
      if (isCurrent(scope)) setBusy('')
    }
  }

  async function downloadDocument(item) {
    if (item.is_stale && !window.confirm('This draft predates changes to the project. Download this older version?')) return
    const projectId = activeId
    const scope = scopeRef.current
    setBusy(`download-${item.id}`)
    setError('')
    try {
      const response = await fetch(`/api/ba/projects/${projectId}/deliverables/${item.id}/download`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!response.ok) {
        const data = await response.json().catch(() => null)
        throw new Error(getErrorMessage(data, `Download failed (${response.status}).`))
      }
      if (!isCurrent(scope)) return
      const blob = await response.blob()
      if (!isCurrent(scope)) return
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      const extension = response.headers.get('content-type')?.includes('application/pdf') ? 'pdf' : 'md'
      link.download = `${item.deliverable_key}.${extension}`
      document.body.appendChild(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (cause) {
      if (isCurrent(scope)) setError(cause.message)
    } finally {
      if (isCurrent(scope)) setBusy('')
    }
  }

  async function downloadSource(source) {
    const projectId = activeId
    const scope = scopeRef.current
    setBusy(`source-${source.id}`)
    setError('')
    try {
      const response = await fetch(`/api/ba/projects/${projectId}/sources/${source.id}/download`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!response.ok) throw new Error(`Source download failed (${response.status}).`)
      if (!isCurrent(scope)) return
      const blob = await response.blob()
      if (!isCurrent(scope)) return
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = source.ref
      document.body.appendChild(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (cause) {
      if (isCurrent(scope)) setError(cause.message)
    } finally {
      if (isCurrent(scope)) setBusy('')
    }
  }

  function chooseView(id) {
    if (!activeProject) { setView('workspace'); setSidebarOpen(false); return }
    setView(id)
    setSidebarOpen(false)
    if (id === 'deliverables') { loadDeliverables(); loadCatalog() }
    if (id === 'evidence') loadFacts()
  }

  const hasNoProjects = !!token && projectsLoaded && projects.length === 0
  const spanBySourceAndKey = new Map(facts
    .filter((fact) => fact.subject_type === 'SourceSpan' && fact.predicate === 'evidence' && fact.value?.text)
    .map((fact) => [`${fact.source_id}:${fact.subject_key}`, fact.value]))
  const citationsByRequirement = new Map()
  for (const fact of facts) {
    if (fact.subject_type !== 'Requirement' || fact.predicate !== 'derived_from' || fact.object_type !== 'SourceSpan') continue
    const span = spanBySourceAndKey.get(`${fact.source_id}:${fact.object_key}`)
    if (!span) continue
    const key = `${fact.source_id}:${fact.subject_key}`
    citationsByRequirement.set(key, [...(citationsByRequirement.get(key) || []), span])
  }
  const findings = facts.filter((fact) => fact.value != null && !['Gap', 'SourceSpan', 'ConversationTurn', 'ClarificationQuestion'].includes(fact.subject_type) && !['derived_from', 'evidence'].includes(fact.predicate))
    .map((fact) => ({ ...fact, display: describeFact(fact), citations: citationsByRequirement.get(`${fact.source_id}:${fact.subject_key}`) || [] }))
  const analyzedSourceIds = new Set(facts.filter((fact) => fact.subject_type === 'SourceAnalysis' && fact.predicate === 'completed').map((fact) => fact.source_id))
  const failedSourceIds = [...new Set(facts.filter((fact) => fact.predicate === 'extraction_failure').map((fact) => fact.source_id))].filter((id) => !analyzedSourceIds.has(id))
  const failedSources = sources.filter((source) => failedSourceIds.includes(source.id))
  const namedDocuments = deliverables.map((item) => ({ ...item, name: documentName(item.deliverable_key) }))
  const namedCatalog = catalog.map((item) => ({ ...item, name: documentName(item.key) }))
  const namedOpenDocument = openDocument ? { ...openDocument, name: documentName(openDocument.deliverable_key) } : null

  return (
    <div className="page-canvas">
      <div className="ambient ambient-one" /><div className="ambient ambient-two" />
      <div className={`app-shell ${activeProject ? '' : 'without-project'}`}>
        <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
          <div className="brand"><AthenaMark /><div><strong>athena</strong><small>BUSINESS ANALYST</small></div></div>
          <button className="project-mini" type="button" onClick={() => token ? setModal('project') : setModal('connect')}>
            <span className="project-mini-icon"><FolderOpen size={17} /></span>
            <span><small>ACTIVE PROJECT</small><strong>{activeProject?.name || 'Create a project'}</strong></span>
            <ChevronDown size={15} />
          </button>
          <div className="side-section-label">YOUR SPACE</div>
          <nav className="side-nav" aria-label="Main navigation">
            {navigation.map(({ id, label, icon: Icon }) => (
              <button key={id} type="button" className={`nav-item ${view === id ? 'active' : ''}`} onClick={() => chooseView(id)}>
                <Icon size={19} strokeWidth={1.8} /><span>{label}</span>{id === 'evidence' && findings.length > 0 && <em>{findings.length}</em>}
              </button>
            ))}
          </nav>
          <div className="sidebar-spacer" />
          <div className="side-art" aria-hidden="true" />
          <div className="side-bottom">
            <div className="side-bottom-head"><BookOpen size={16} /><span>Project evidence</span></div>
            <p>Sources, facts, decisions, and deliverables stay within the selected project.</p>
          </div>
          <button type="button" className="side-account" onClick={() => setModal(token ? 'account' : 'connect')}>
            <span className="account-avatar"><ShieldCheck size={17} /></span>
            <span><strong>{user?.name || (token ? 'Signed in' : 'Sign in to Athena')}</strong><small>{user?.email || (token ? 'Session active' : 'Create or open your account')}</small></span>
            <ArrowUpRight size={16} />
          </button>
        </aside>

        <main className={`main-panel${activeProject && view === 'workspace' ? ' conversation-mode' : ''}`}>
          <header className="topbar">
            <button className="icon-button mobile-menu" type="button" onClick={() => setSidebarOpen(!sidebarOpen)} aria-label="Toggle menu"><Menu size={21} /></button>
            <div className="breadcrumb"><span>{activeProject?.name || 'Projects'}</span><span className="slash">/</span><strong>{navigation.find((item) => item.id === view)?.label}</strong></div>
            <div className="top-actions">
              <span className={`connection-dot ${token ? 'is-online' : ''}`} />
              <span className="connection-label">{token ? 'Signed in' : 'Guest view'}</span>
              <button className="top-project-button" type="button" onClick={() => setModal(token ? 'project' : 'connect')}><Plus size={16} /> New project</button>
            </div>
          </header>

          {error && <div className="notice" role="alert"><CircleHelp size={17} /><span>{error}</span><button type="button" onClick={() => setError('')} aria-label="Dismiss message"><X size={15} /></button></div>}

          {token && !projectsLoaded && <div className="loading-projects"><LoaderCircle className="spin" size={19} /> Opening projects...</div>}
          {(!token || hasNoProjects) && <section className="project-empty">
            <div className="project-empty-copy">
              <span className="empty-icon"><FolderOpen size={25} /></span>
              <h1>{token ? 'Create your first project.' : 'Open your analyst workspace.'}</h1>
              <p>{token ? 'Athena organizes every source, fact, question, and deliverable around a project. Name the work to begin.' : 'Sign in to access your projects, or create an account to start one.'}</p>
              {token ? <form className="project-start-form" onSubmit={createProject}><label htmlFor="first-project-name">Project name</label><div><input id="first-project-name" value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="e.g. Customer onboarding redesign" required maxLength={120} /><button className="primary-action" type="submit" disabled={!projectName.trim() || !!busy}>{busy === 'project' ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />} Create project</button></div></form> : <button className="primary-action" type="button" onClick={() => setModal('connect')}>Sign in or create an account <ArrowRight size={16} /></button>}
            </div>
            <div className="project-empty-art" aria-hidden="true"><img src={`${import.meta.env.BASE_URL}athena-illustration.png`} alt="" /></div>
          </section>}

          {activeProject && view === 'workspace' && <ProjectConversation
            project={activeProject} sources={sources} findings={findings} documents={namedDocuments}
            failedSourceIds={failedSourceIds}
            uploadReport={uploadReport} feedback={feedback} busy={busy} fileRef={fileRef}
            onNavigate={chooseView} onUpload={uploadFile} prompt={prompt} onPromptChange={setPrompt}
            onSubmitPrompt={submitPrompt}
            onTranscribeAudio={(file) => {
              const form = new FormData()
              form.append('audio', file)
              return request(`/api/ba/projects/${activeId}/chat/voice/transcriptions`, token, { method: 'POST', body: form })
            }}
            onSynthesizeSpeech={(text) => requestAudio(`/api/ba/projects/${activeId}/chat/voice/speech`, token, { text })}
            activity={activity} userName={user?.name} question={clarification} questionsComplete={questionsComplete} conflictNotice={conflictNotice} analysisSteps={analysisSteps} liveAnswer={liveAnswer} liveThinking={liveThinking}
            catalog={namedCatalog} documentKey={documentKey} onKeyChange={setDocumentKey} onGenerate={generateDocument}
          />}

          {activeProject && view === 'evidence' && <FindingsView
            project={activeProject} findings={findings} sources={sources} busy={busy}
            onNavigate={chooseView} onDownloadSource={downloadSource} onApproveScope={approveScope} questionsComplete={questionsComplete}
          />}

          {activeProject && view === 'deliverables' && <DocumentsView
            token={token} projectId={activeId}
            catalog={namedCatalog} documentKey={documentKey} onKeyChange={setDocumentKey}
            documents={namedDocuments} openDocument={namedOpenDocument} busy={busy}
            findingsCount={findings.length} onGenerate={generateDocument}
            onView={viewDocument} onDownload={downloadDocument} onNavigate={chooseView}
          />}
        </main>

        {activeProject && <aside className="flow-rail">
          <span className="flow-eyebrow">YOUR PROJECT</span>
          <h2>{clarification ? 'Athena has a question' : questionsComplete ? 'Review your scope' : 'Keep building context'}</h2>
          <p>{clarification ? clarification.question : questionsComplete ? 'Check the project narrative and priorities, then approve or revise them in chat.' : 'Add project material and talk through the details with Athena.'}</p>
          <div className="flow-rail-progress">
            <div><strong>{sources.filter((source) => source.kind === 'document').length}</strong><span>sources added</span></div>
            <div><strong>{findings.length}</strong><span>findings recorded</span></div>
            <div><strong>{namedDocuments.length}</strong><span>documents created</span></div>
          </div>
          <button type="button" onClick={() => chooseView(questionsComplete && (sources.length || findings.length) ? 'evidence' : 'workspace')}>
            {questionsComplete && (sources.length || findings.length) ? 'Review project scope' : 'Continue conversation'} <ArrowRight size={17} />
          </button>
          {failedSources.length > 0 && <section className="flow-rail-retry" aria-label="Files that need another pass">
            <strong>{failedSources.length} {failedSources.length === 1 ? 'file needs' : 'files need'} another pass</strong>
            <p>Part of the analysis did not finish. Retrying only re-reads the parts that failed.</p>
            <ul>{failedSources.map((source) => <li key={source.id}>{source.ref}</li>)}</ul>
            <button type="button" onClick={() => retryFailedSources()} disabled={!!busy}>
              {busy === 'retry' ? <LoaderCircle className="spin" size={16} /> : <RotateCcw size={16} />} {busy === 'retry' ? retryProgress || 'Retrying…' : `Retry ${failedSources.length === 1 ? 'analysis' : `all ${failedSources.length}`}`}
            </button>
          </section>}
        </aside>}
        {sidebarOpen && <button type="button" className="mobile-scrim" aria-label="Close menu" onClick={() => setSidebarOpen(false)} />}
      </div>

      {modal === 'connect' && <Modal title={authMode === 'login' ? 'Welcome back' : 'Make a little room for clarity'} description={authMode === 'login' ? 'Sign in to open your projects and evidence.' : 'Create an Athena account and start your first project.'} onClose={() => setModal(null)}><div className="auth-tabs" role="tablist" aria-label="Account access"><button type="button" role="tab" aria-selected={authMode === 'login'} className={authMode === 'login' ? 'selected' : ''} onClick={() => { setAuthMode('login'); setAuthError('') }}>Sign in</button><button type="button" role="tab" aria-selected={authMode === 'register'} className={authMode === 'register' ? 'selected' : ''} onClick={() => { setAuthMode('register'); setAuthError('') }}>Create account</button></div><form className="modal-form auth-form" onSubmit={connect}>{authMode === 'register' && <><label htmlFor="auth-name">Your name</label><input id="auth-name" value={authName} onChange={(event) => setAuthName(event.target.value)} placeholder="How should Athena greet you?" required maxLength={120} autoComplete="name" /></>}<label htmlFor="auth-email">Email</label><input id="auth-email" type="email" value={authEmail} onChange={(event) => setAuthEmail(event.target.value)} placeholder="you@example.com" required autoComplete="email" autoFocus /><label htmlFor="auth-password">Password</label><div className="field-with-icon"><KeyRound size={18} /><input id="auth-password" type="password" value={authPassword} onChange={(event) => setAuthPassword(event.target.value)} placeholder={authMode === 'register' ? 'At least 12 characters' : 'Your password'} required minLength={authMode === 'register' ? 12 : undefined} autoComplete={authMode === 'register' ? 'new-password' : 'current-password'} /></div>{authError && <p className="auth-error" role="alert">{authError}</p>}<button className="primary-action" type="submit" disabled={!authEmail.trim() || !authPassword || !!busy}>{busy === 'auth' ? <LoaderCircle className="spin" size={17} /> : <ArrowRight size={17} />}{authMode === 'login' ? 'Sign in' : 'Create account'}</button></form></Modal>}
      {modal === 'project' && (
        <Modal
          title={projectToDelete ? `Delete “${projectToDelete.name}”?` : 'Open or create a project'}
          description={
            projectToDelete
              ? 'This permanently removes the project and its sources, findings, questions, and documents. This cannot be undone.'
              : 'Choose the project you want to work on, or name a new one.'
          }
          onClose={() => {
            if (!busy) {
              setModal(null)
              setProjectToDelete(null)
              setDeleteError('')
            }
          }}
        >
          {projectToDelete ? (
            <div className="project-delete-confirm">
              <p className="project-delete-target">
                Permanently delete <strong>“{projectToDelete.name}”</strong>?
              </p>
              {deleteError && (
                <div className="notice project-delete-error" role="alert">
                  <CircleHelp size={16} />
                  <span>{deleteError}</span>
                </div>
              )}
              <div className="project-delete-actions">
                <button
                  type="button"
                  className="secondary-action"
                  disabled={busy === 'delete-project'}
                  onClick={() => {
                    setProjectToDelete(null)
                    setDeleteError('')
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="danger-action"
                  disabled={busy === 'delete-project'}
                  onClick={() => deleteProject(projectToDelete.id)}
                >
                  {busy === 'delete-project' ? (
                    <>
                      <LoaderCircle className="spin" size={16} />
                      <span>Deleting project...</span>
                    </>
                  ) : (
                    <>
                      <Trash2 size={16} />
                      <span>Delete project</span>
                    </>
                  )}
                </button>
              </div>
            </div>
          ) : (
            <>
              <form className="modal-form" onSubmit={createProject}>
                <label htmlFor="project-name">New project name</label>
                <input
                  id="project-name"
                  value={projectName}
                  onChange={(event) => setProjectName(event.target.value)}
                  placeholder="e.g. Hospital inventory system"
                  autoFocus
                />
                <button className="primary-action" type="submit" disabled={!projectName.trim() || !!busy}>
                  {busy === 'project' ? <LoaderCircle className="spin" size={17} /> : <Plus size={17} />} Create project
                </button>
              </form>
              {projects.length > 0 && (
                <div className="project-picker">
                  <span>OPEN AN EXISTING PROJECT</span>
                  {projects.map((project) => (
                    <div key={project.id} className="project-picker-row">
                      <button
                        type="button"
                        className={`project-picker-select ${project.id === activeId ? 'is-active' : ''}`}
                        onClick={() => {
                          changeProject(project.id)
                          sessionStorage.setItem('athena_project', project.id)
                          setView('workspace')
                          setModal(null)
                        }}
                      >
                        <FolderOpen size={16} />
                        <span className="project-picker-name">{project.name}</span>
                        {project.id === activeId && <span className="project-picker-badge">Active</span>}
                        <ArrowRight size={15} />
                      </button>
                      <button
                        type="button"
                        className="project-picker-delete"
                        aria-label={`Delete project ${project.name}`}
                        title={`Delete project ${project.name}`}
                        disabled={!!busy}
                        onClick={(event) => {
                          event.stopPropagation()
                          setProjectToDelete(project)
                          setDeleteError('')
                        }}
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </Modal>
      )}
      {modal === 'account' && <Modal title={user?.name || 'Your account'} description={user?.email || 'Your Athena session is active in this browser tab.'} onClose={() => setModal(null)}><button className="secondary-action" type="button" onClick={disconnect}>Sign out <ArrowRight size={17} /></button></Modal>}
    </div>
  )
}
