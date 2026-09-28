import { useEffect, useRef, useState } from 'react'
import { ArrowRight, FileCheck2, LoaderCircle, Mic, Paperclip, Send, Square, Volume2 } from 'lucide-react'
import { contextEntries } from './ProjectViews'

function AnswerEvaluation({ evaluation }) {
  if (!evaluation) return null
  const behaviorScores = evaluation.behavior_scores
  const behaviorPanel = behaviorScores && behaviorScores.status !== 'disabled' ? <details className="answer-evaluation">
    <summary>Span-01 behavior scores{behaviorScores.status === 'completed' ? '' : ' · unavailable'}</summary>
    {behaviorScores.status === 'completed'
      ? <><p>OpenRouter returns a probability that each behavior is present; it does not provide a separate “not observable” score.</p><ul>{behaviorScores.behaviors?.map((behavior) => <li key={behavior.id}>
        <span><strong>{behavior.label}</strong><b>{Math.round(behavior.p_present * 100)}% present</b></span>
        <progress max="1" value={behavior.p_present} aria-label={`${behavior.label}: ${Math.round(behavior.p_present * 100)} percent present`} />
      </li>)}</ul></>
      : <p>{behaviorScores.message || 'Athena could not score this answer.'}</p>}
  </details> : null
  if (evaluation.status !== 'completed') {
    return <>{behaviorPanel}<details className="answer-evaluation"><summary>LLM review unavailable</summary><p>{evaluation.message || 'Athena could not score this answer.'}</p></details></>
  }
  return <><details className="answer-evaluation">
    <summary>LLM review · {evaluation.overall_score}/5 · {evaluation.ready_for_user ? 'ready for use' : 'needs review'}</summary>
    <p>{evaluation.feedback}</p>
    {evaluation.revision_count > 0 && <p>Applied evaluator feedback and checked the revised answer.</p>}
    {evaluation.attempts?.length > 1 && <p>Review scores: {evaluation.attempts.map((attempt) => attempt.overall_score).join(' → ')} / 5</p>}
    {evaluation.revision_message && <p>{evaluation.revision_message}</p>}
    <ul>{evaluation.dimensions?.map((dimension) => <li key={dimension.key}>
      <span><strong>{dimension.label}</strong><b>{dimension.score}/5</b></span>
      <p>{dimension.rationale}</p>
      {dimension.suggested_improvement && <p className="answer-evaluation-suggestion">Suggested improvement: {dimension.suggested_improvement}</p>}
    </li>)}</ul>
  </details>{behaviorPanel}</>
}

export function ProjectConversation({
  project, sources, findings, failedSourceIds, uploadReport, busy, fileRef,
  onNavigate, onUpload, prompt, onPromptChange,
  onSubmitPrompt, activity, userName, question, questionsComplete, conflictNotice, analysisSteps, liveAnswer, liveThinking,
  catalog, documentKey, onKeyChange, onGenerate, onTranscribeAudio, onSynthesizeSpeech,
}) {
  const threadRef = useRef(null)
  const stickToBottom = useRef(true)
  const dragDepth = useRef(0)
  const recorderRef = useRef(null)
  const recorderStreamRef = useRef(null)
  const recorderChunksRef = useRef([])
  const speechRecognitionRef = useRef(null)
  const speechRecognitionActiveRef = useRef(false)
  const speechRestartTimerRef = useRef(null)
  const liveTranscriptRef = useRef('')
  const basePromptRef = useRef('')
  const audioRef = useRef(null)
  const audioUrlRef = useRef(null)
  const [dragging, setDragging] = useState(false)
  const [voiceState, setVoiceState] = useState('')
  const [voiceNotice, setVoiceNotice] = useState(null)
  const [liveTranscript, setLiveTranscript] = useState('')
  const [liveCaptionState, setLiveCaptionState] = useState('')
  const [speechLoadingId, setSpeechLoadingId] = useState('')
  const [speakingId, setSpeakingId] = useState('')
  const materialCount = sources.filter((source) => source.kind === 'document').length
  const firstName = userName?.trim().split(/\s+/)[0] || 'there'
  const visiblePrompt = (voiceState === 'recording' || voiceState === 'transcribing') && liveTranscript
    ? [basePromptRef.current, liveTranscript].filter(Boolean).join(basePromptRef.current ? '\n' : '')
    : prompt
  // Follow new output only while the reader is already at the bottom, like standard chat apps;
  // scrolling up to read earlier turns is never yanked back down.
  useEffect(() => {
    const thread = threadRef.current
    if (thread && stickToBottom.current) thread.scrollTop = thread.scrollHeight
  }, [activity.length, question?.gap_key, busy, analysisSteps?.length, liveAnswer, liveThinking, uploadReport, liveTranscript, voiceState])

  useEffect(() => () => {
    stopLiveRecognition()
    if (recorderRef.current) {
      recorderRef.current.onstop = null
      if (recorderRef.current.state !== 'inactive') recorderRef.current.stop()
      recorderRef.current = null
    }
    recorderStreamRef.current?.getTracks().forEach((track) => track.stop())
    if (audioRef.current) audioRef.current.pause()
    if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current)
  }, [project.id])

  useEffect(() => {
    setVoiceState('')
    setVoiceNotice(null)
    setLiveTranscript('')
    setLiveCaptionState('')
    setSpeechLoadingId('')
    setSpeakingId('')
  }, [project.id])

  function stopLiveRecognition() {
    speechRecognitionActiveRef.current = false
    if (speechRestartTimerRef.current) {
      window.clearTimeout(speechRestartTimerRef.current)
      speechRestartTimerRef.current = null
    }
    const recognition = speechRecognitionRef.current
    speechRecognitionRef.current = null
    if (recognition) {
      recognition.onend = null
      recognition.onerror = null
      recognition.onresult = null
      try { recognition.stop() } catch { /* The browser may already have stopped it. */ }
    }
  }

  function startLiveRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SpeechRecognition) {
      setLiveCaptionState('unsupported')
      return
    }

    let recognition
    try {
      recognition = new SpeechRecognition()
    } catch {
      setLiveCaptionState('unavailable')
      return
    }
    recognition.continuous = true
    recognition.interimResults = true
    recognition.lang = navigator.language || 'en-US'
    speechRecognitionRef.current = recognition
    speechRecognitionActiveRef.current = true
    setLiveCaptionState('listening')

    recognition.onresult = (event) => {
      if (!speechRecognitionActiveRef.current) return
      const parts = []
      for (let index = 0; index < event.results.length; index += 1) {
        const text = event.results[index]?.[0]?.transcript?.trim()
        if (text) parts.push(text)
      }
      const transcript = parts.join(' ').replace(/\s+/g, ' ').trim()
      liveTranscriptRef.current = transcript
      setLiveTranscript(transcript)
    }

    recognition.onerror = (event) => {
      if (['not-allowed', 'service-not-allowed', 'audio-capture', 'language-not-supported', 'network'].includes(event.error)) {
        speechRecognitionActiveRef.current = false
        setLiveCaptionState('unavailable')
      }
    }

    recognition.onend = () => {
      if (!speechRecognitionActiveRef.current || recorderRef.current?.state !== 'recording' || speechRestartTimerRef.current) return
      speechRestartTimerRef.current = window.setTimeout(() => {
        speechRestartTimerRef.current = null
        if (!speechRecognitionActiveRef.current || recorderRef.current?.state !== 'recording') return
        try {
          recognition.start()
        } catch {
          speechRecognitionActiveRef.current = false
          setLiveCaptionState('unavailable')
        }
      }, 250)
    }

    try {
      recognition.start()
    } catch {
      speechRecognitionActiveRef.current = false
      setLiveCaptionState('unavailable')
    }
  }

  async function startVoiceInput() {
    setVoiceNotice(null)
    setLiveTranscript('')
    liveTranscriptRef.current = ''
    basePromptRef.current = prompt.trim()
    setLiveCaptionState('')
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder || !onTranscribeAudio) {
      setVoiceNotice({ type: 'error', text: 'Voice recording is not available in this browser.' })
      return
    }
    try {
      stopSpeech()
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      recorderStreamRef.current = stream
      const supportedType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus']
        .find((type) => MediaRecorder.isTypeSupported(type))
      const recorder = new MediaRecorder(stream, supportedType ? { mimeType: supportedType } : undefined)
      recorderChunksRef.current = []
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) recorderChunksRef.current.push(event.data)
      }
      recorder.onstop = async () => {
        recorderRef.current = null
        stopLiveRecognition()
        recorderStreamRef.current?.getTracks().forEach((track) => track.stop())
        recorderStreamRef.current = null
        const chunks = recorderChunksRef.current
        recorderChunksRef.current = []
        const mimeType = recorder.mimeType || chunks[0]?.type || 'audio/webm'
        const extension = mimeType.includes('mp4') ? 'm4a' : mimeType.includes('ogg') ? 'ogg' : mimeType.includes('wav') ? 'wav' : 'webm'
        const recording = new File(chunks, `voice-input.${extension}`, { type: mimeType })
        if (!recording.size) {
          setVoiceState('')
          setLiveCaptionState('')
          setVoiceNotice({ type: 'error', text: 'The recording was empty. Try again.' })
          return
        }
        setVoiceState('transcribing')
        try {
          const result = await onTranscribeAudio(recording)
          const transcript = result?.text?.trim()
          if (!transcript) throw new Error('No speech was detected. Try again or type your message.')
          const basePrompt = basePromptRef.current
          onPromptChange([basePrompt, transcript].filter(Boolean).join(basePrompt ? '\n' : ''))
          setLiveTranscript('')
          liveTranscriptRef.current = ''
          setLiveCaptionState('')
          setVoiceNotice({ type: 'success', text: 'OpenRouter transcript ready. Review it, then send.' })
        } catch (cause) {
          const liveDraft = liveTranscriptRef.current.trim()
          if (liveDraft) {
            const basePrompt = basePromptRef.current
            onPromptChange([basePrompt, liveDraft].filter(Boolean).join(basePrompt ? '\n' : ''))
            setVoiceNotice({ type: 'error', text: 'OpenRouter could not finish the transcript. The live caption is in the composer; review it before sending.' })
          } else {
            setVoiceNotice({ type: 'error', text: cause.message || 'Transcription failed. Try again or type your message.' })
          }
          setLiveTranscript('')
          liveTranscriptRef.current = ''
          setLiveCaptionState('')
        } finally {
          setVoiceState('')
        }
      }
      recorderRef.current = recorder
      recorder.start()
      setVoiceState('recording')
      startLiveRecognition()
    } catch (cause) {
      stopLiveRecognition()
      recorderStreamRef.current?.getTracks().forEach((track) => track.stop())
      recorderStreamRef.current = null
      setVoiceState('')
      setLiveCaptionState('')
      setVoiceNotice({ type: 'error', text: cause.name === 'NotAllowedError' ? 'Allow microphone access to record a message.' : 'Could not start the microphone. Try again or type your message.' })
    }
  }

  function stopVoiceInput() {
    if (recorderRef.current?.state === 'recording') {
      stopLiveRecognition()
      recorderRef.current.stop()
      setVoiceState('transcribing')
    }
  }

  function stopSpeech() {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current)
      audioUrlRef.current = null
    }
    setSpeakingId('')
  }

  async function playSpeech(turnId, text) {
    if (speakingId === turnId) {
      stopSpeech()
      return
    }
    if (!onSynthesizeSpeech) return
    stopSpeech()
    setVoiceNotice(null)
    setSpeechLoadingId(turnId)
    try {
      const audioBlob = await onSynthesizeSpeech(text)
      if (!audioBlob?.size) throw new Error('Speech playback returned no audio.')
      const url = URL.createObjectURL(audioBlob)
      audioUrlRef.current = url
      const player = new Audio(url)
      audioRef.current = player
      player.onended = stopSpeech
      player.onerror = () => {
        stopSpeech()
        setVoiceNotice({ type: 'error', text: 'Speech playback failed. You can still read the reply.' })
      }
      setSpeakingId(turnId)
      await player.play()
    } catch (cause) {
      stopSpeech()
      setVoiceNotice({ type: 'error', text: cause.message || 'Speech playback failed. You can still read the reply.' })
    } finally {
      setSpeechLoadingId('')
    }
  }

  function handleSubmitPrompt(event) {
    setVoiceNotice(null)
    onSubmitPrompt(event)
  }

  return <section className={`conversation-page ${dragging ? 'is-dragging' : ''}`} aria-label={`${project.name} conversation`}
    onDragEnter={(event) => { event.preventDefault(); dragDepth.current += 1; setDragging(true) }}
    onDragOver={(event) => event.preventDefault()}
    onDragLeave={(event) => { event.preventDefault(); dragDepth.current -= 1; if (dragDepth.current <= 0) { dragDepth.current = 0; setDragging(false) } }}
    onDrop={(event) => { event.preventDefault(); dragDepth.current = 0; setDragging(false); if (event.dataTransfer.files.length) onUpload({ target: { files: event.dataTransfer.files, value: '' } }) }}>
    {dragging && <div className="conversation-drop-cue">Drop project files to add them to Athena</div>}
    <header className="conversation-header">
      <div><h1>{project.name}</h1><p>Talk through the project with Athena. Add material whenever it helps.</p></div>
      <button type="button" className="conversation-findings-link" onClick={() => onNavigate('evidence')}>Project scope <ArrowRight size={16} /></button>
    </header>
    <div className="conversation-thread" role="log" aria-live="polite" ref={threadRef}
      onScroll={(event) => { const el = event.currentTarget; stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80 }}>
      <div className="conversation-turn agent-turn"><span className="turn-avatar">A</span><div className="turn-body"><strong>Athena</strong><p>Hey {firstName}, what should I know about this project? Tell me what you are trying to achieve, or add your briefs, notes, requirements, and other files. I’ll read them and ask about anything that needs a decision.</p></div></div>
      {[...activity].reverse().map((item) => {
        const spokenText = item.investigating ? item.result?.answer : item.result?.reply
        const replyText = item.result?.reply || ''
        const hidesRoutineAcknowledgment = item.message && (
          replyText.startsWith("I couldn't confirm anything new from that message.")
          || replyText.startsWith('Thanks, I recorded ')
          || replyText.startsWith('Got it.')
          || replyText.startsWith('I’ve got it.')
        )
        return <div className="conversation-event" key={item.id}>
        {item.clarification && <div className="conversation-turn agent-turn"><span className="turn-avatar">A</span><div className="turn-body"><strong>Athena · question</strong><p>{item.clarification}</p></div></div>}
        {(item.message || item.response || item.file) && <div className="conversation-turn user-turn"><div className="turn-body"><strong>You</strong><p>{item.message || item.response || `Added ${item.file}`}</p></div></div>}
        {!item.pending && !item.response && (item.document || item.investigating || item.conflict || item.file || (item.result?.reply && !question && !hidesRoutineAcknowledgment)) && <div className="conversation-turn agent-turn"><span className="turn-avatar">A</span><div className="turn-body"><div className="turn-heading"><strong>Athena</strong>{spokenText && onSynthesizeSpeech && <button type="button" className="turn-audio-control" onClick={() => playSpeech(item.id, spokenText)} disabled={!!speechLoadingId && speechLoadingId !== item.id} aria-label={speakingId === item.id ? 'Stop reading reply' : 'Read reply aloud'} title={speakingId === item.id ? 'Stop reading' : 'Read reply aloud'}>{speechLoadingId === item.id ? <LoaderCircle className="spin" size={15} /> : speakingId === item.id ? <Square size={14} /> : <Volume2 size={16} />}</button>}</div>
          {item.document ? <><p>I created a {item.document} draft from the current project record.</p>{item.documentContent ? <details><summary>Read draft</summary><pre>{item.documentContent}</pre></details> : <button className="inline-link" type="button" onClick={() => onNavigate('deliverables')}>Open draft <ArrowRight size={15} /></button>}</>
            : item.investigating ? <>{item.steps?.length > 0 && <details className="conversation-trace"><summary>View agent steps</summary>{item.steps.filter((step) => step.type === 'tool_call').map((step, index) => <p key={index}>{step.specialist || 'Project tool'} · {step.tool.replaceAll('_', ' ')}</p>)}</details>}<p>{item.result?.answer || 'I could not find an answer in the project record.'}</p><AnswerEvaluation evaluation={item.result?.evaluation} /></>
              : item.conflict ? <p role="alert">{item.conflict}</p>
                : item.file ? <p>I’ve processed the uploaded material and updated the project record. I’ll ask about anything still unclear.</p>
                  : <>{item.steps?.length > 0 && <details className="conversation-trace"><summary>View agent steps</summary>{item.steps.filter((step) => step.type === 'tool_call').map((step, index) => <p key={index}>{step.specialist || 'Project tool'} · {step.tool.replaceAll('_', ' ')}</p>)}</details>}{item.result?.reply && <p>{item.result.reply}</p>}</>}
        </div></div>}
      </div>
      })}
      {(voiceState === 'recording' || voiceState === 'transcribing') && <div className="conversation-turn user-turn live-voice-turn" aria-live="polite">
        <div className="turn-body">
          <strong>You · {voiceState === 'recording' ? 'speaking' : 'transcribing'}</strong>
          <p>{liveTranscript || (voiceState === 'transcribing' ? 'Transcribing the full recording…' : liveCaptionState === 'listening' ? 'Listening for speech…' : 'Live captions are unavailable here. The final transcript will appear in the composer.')}</p>
        </div>
      </div>}
      {uploadReport && <div className="conversation-turn agent-turn"><span className="turn-avatar">A</span><div className="turn-body"><strong>File analysis</strong><p>{busy === 'upload' ? `Processing ${uploadReport.processed} of ${uploadReport.total} files…` : `${uploadReport.completed} of ${uploadReport.total} files added. ${uploadReport.requirements} requirements, ${uploadReport.contextFacts} context facts, and ${uploadReport.gaps} gaps found.`}</p>
        {busy !== 'upload' && uploadReport.understanding && <div className="upload-understanding"><strong>What I understood from your files</strong>{[
          ['Project purpose', uploadReport.understanding.project_purpose || uploadReport.understanding.vision],
          ['Problem', uploadReport.understanding.problem_statement],
          ['Business goals', uploadReport.understanding.business_goals],
          ['Must have', uploadReport.understanding.must_have_features],
          ['Should have', uploadReport.understanding.should_have_features],
          ['Constraints', uploadReport.understanding.constraints],
          ['Stakeholders', uploadReport.understanding.stakeholders],
        ].filter(([, value]) => typeof value === 'string' ? value.trim() : Array.isArray(value) && value.length > 0).map(([label, value]) => <p key={label}><b>{label}:</b> {Array.isArray(value) ? value.join('; ') : value}</p>)}
          {Object.entries(uploadReport.understanding.business_context || {}).map(([section, fields]) => {
            const known = contextEntries(fields).filter(([, value]) => value != null)
            return known.length ? <details key={section}><summary>{section.replaceAll('_', ' ')} · {known.length} details</summary>{known.map(([label, value], index) => <p key={index}><b>{label}{value ? ':' : ''}</b> {value}</p>)}</details> : null
          })}</div>}
        {uploadReport.partFindings?.length > 0 && <details className="conversation-trace" open={busy === 'upload'}><summary>What I found in each file</summary>{uploadReport.partFindings.map((finding, index) => <p key={index}>{finding}</p>)}</details>}
        {uploadReport.analysisIssues?.map((issue, index) => <p className="conversation-issue" key={index}>{issue.name}: {issue.reason}</p>)}
        {uploadReport.failed?.map((issue, index) => <p className="conversation-issue" key={index}>{issue.name} could not be added: {issue.reason}</p>)}
      </div></div>}
      {question && <div className="conversation-turn agent-turn active-question"><span className="turn-avatar">A</span><div className="turn-body"><div className="turn-heading"><strong>Athena</strong>{onSynthesizeSpeech && <button type="button" className="turn-audio-control" onClick={() => playSpeech(question.gap_key || 'active-question', question.question)} disabled={!!speechLoadingId && speechLoadingId !== (question.gap_key || 'active-question')} aria-label={speakingId === (question.gap_key || 'active-question') ? 'Stop reading question' : 'Read question aloud'} title={speakingId === (question.gap_key || 'active-question') ? 'Stop reading' : 'Read question aloud'}>{speechLoadingId === (question.gap_key || 'active-question') ? <LoaderCircle className="spin" size={15} /> : speakingId === (question.gap_key || 'active-question') ? <Square size={14} /> : <Volume2 size={16} />}</button>}</div><p>{question.question}</p></div></div>}
      {conflictNotice && <p className="conversation-issue" role="alert">{conflictNotice}</p>}
      {(analysisSteps?.length > 0 || liveAnswer || liveThinking) && <div className="conversation-turn agent-turn"><span className="turn-avatar">A</span><div className="turn-body conversation-live-steps"><strong>{busy === 'upload' ? 'Athena is reading your files' : 'Athena is working'}</strong>
        {analysisSteps?.map((step, index) => <p key={index}>{step.message || `${step.specialist || 'Project tool'} · ${step.tool.replaceAll('_', ' ')}`}</p>)}
        {liveThinking && <details className="conversation-thinking" open={!liveAnswer}><summary>Thinking</summary><p>{liveThinking}</p></details>}
        {liveAnswer && <p className="conversation-live-answer">{liveAnswer}</p>}
      </div></div>}
      {!question && questionsComplete && (materialCount > 0 || findings.length > 0) && <div className="conversation-turn agent-turn"><span className="turn-avatar">A</span><div className="turn-body"><strong>Athena</strong><p>We can keep shaping this whenever you like. Review the project scope, or tell me what you’d like to change next.</p><button className="inline-link" type="button" onClick={() => onNavigate('evidence')}>Review project scope <ArrowRight size={15} /></button></div></div>}
      {busy === 'analysis' || busy === 'answer' || busy === 'upload' || busy === 'generate' ? <p className="conversation-working"><LoaderCircle className="spin" size={16} /> {busy === 'upload' ? 'Reading your files' : busy === 'generate' ? 'Creating the draft' : 'Athena is working'}</p> : null}
    </div>
    <div className="conversation-composer">
      {failedSourceIds.length > 0 && <p className="conversation-issue">Some saved files still need analysis. Their details are listed above.</p>}
      <form onSubmit={handleSubmitPrompt}><label className="sr-only" htmlFor="project-message">Message Athena</label><textarea id="project-message" value={visiblePrompt} onChange={(event) => onPromptChange(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); if (!voiceState) handleSubmitPrompt(event) } }} placeholder={question ? 'Answer this question, or ask Athena about your project…' : 'Describe the project, ask a question, or request a change…'} rows="1" disabled={!!busy || !!voiceState} /><div className="composer-actions"><input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md,.sql,.py,.js,.jsx,.ts,.tsx,.json,.yaml,.yml" multiple hidden onChange={onUpload} /><button type="button" onClick={() => fileRef.current?.click()} disabled={!!busy || !!voiceState}><Paperclip size={18} /> Add files</button><button type="button" className={`composer-voice ${voiceState === 'recording' ? 'is-recording' : ''}`} onClick={voiceState === 'recording' ? stopVoiceInput : startVoiceInput} disabled={!!busy || voiceState === 'transcribing'} aria-label={voiceState === 'recording' ? 'Stop recording' : voiceState === 'transcribing' ? 'Transcribing recording' : 'Record a voice message'}>{voiceState === 'transcribing' ? <LoaderCircle className="spin" size={16} /> : voiceState === 'recording' ? <Square size={14} /> : <Mic size={17} />}<span>{voiceState === 'recording' ? 'Stop' : voiceState === 'transcribing' ? 'Transcribing' : 'Voice'}</span></button><span>{materialCount} {materialCount === 1 ? 'file' : 'files'} in this project</span><button className="composer-send" type="submit" disabled={!prompt.trim() || !!busy || !!voiceState} aria-label="Send message"><Send size={18} /></button></div></form>
      {voiceState === 'recording' && <div className={`voice-live-panel ${liveCaptionState === 'unavailable' || liveCaptionState === 'unsupported' ? 'is-limited' : ''}`}>
        <span className="voice-live-dot" aria-hidden="true" />
        <div className="voice-live-copy">
          <strong role="status">{liveCaptionState === 'listening' ? (liveTranscript ? 'Live captions' : 'Listening live') : 'Live captions unavailable'}</strong>
          <p>{liveCaptionState === 'listening' ? 'Your words appear in the composer as you speak.' : 'Your recording will still be transcribed when you stop.'}</p>
          <span className="sr-only" aria-live="polite">{liveTranscript}</span>
          <small>Live preview from your browser. OpenRouter transcribes the full recording when you stop.</small>
        </div>
      </div>}
      {voiceState === 'transcribing' && <p className="voice-notice" role="status"><LoaderCircle className="spin" size={14} /> Transcribing the full recording with OpenRouter…</p>}
      {voiceNotice && <p className={`voice-notice ${voiceNotice.type === 'error' ? 'is-error' : ''}`} role={voiceNotice.type === 'error' ? 'alert' : 'status'}>{voiceNotice.text}</p>}
      {!!catalog.length && (materialCount > 0 || findings.length > 0) && <div className="composer-deliverable"><FileCheck2 size={16} /><label htmlFor="chat-document-kind">Create a deliverable</label><select id="chat-document-kind" value={documentKey} onChange={(event) => onKeyChange(event.target.value)} disabled={!!busy}>{catalog.map((item) => <option key={item.key} value={item.key}>{item.name}</option>)}</select><button type="button" onClick={() => onGenerate()} disabled={!!busy}>Generate <ArrowRight size={15} /></button></div>}
    </div>
  </section>
}
