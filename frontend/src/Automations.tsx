import { useCallback, useEffect, useId, useRef, useState, type RefObject } from 'react'
import {
  deleteAutomation,
  deleteKeyJoinJob,
  listAutomations,
  listFiles,
  listKeyJoinJobs,
  outcomeText,
  runAutomation,
  uploadFiles,
  type Automation,
  type AutomationRunResult,
  type KeyJoinJob,
  type StoredFile,
} from './api'
import ConfirmDialog from './ConfirmDialog'
import { ProgressPanel } from './Progress'
import { REVIEW_STAGES } from './progressStages'
import ReviewPanel from './ReviewPanel'
import { RunOutcomeText } from './RunOutcome'
import { formatDuration, formatTime, jobTimeText, STATUS_CLASS } from './runFormat'
import { RunDownloadLink } from './RunDownloadLink'
import { NavLink } from './Workflow'

function errorText(err: unknown) {
  return err instanceof Error ? err.message : String(err)
}

/** 자동화·저장한 작업: 사용자가 저장해 둔 것을 한 곳에서 찾는다. 두 기능은 따로 저장되고(API·DB 그대로) 이 화면은 두 목록을 함께 보인다.
 *  - 통합 자동화: 새 파일을 올려 저장된 규칙을 바로 적용한다 (새 열 등 확인할 항목만 검토) — 예전 '자동화' 화면 그대로.
 *  - 공통 key 저장한 작업(/keyjoin/jobs, 본인 것만): 보기·삭제와 '적용하러 가기'(그 작업을 고른 채 공통 key 연결 화면으로). */
function Automations() {
  const [automations, setAutomations] = useState<Automation[] | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [jobCount, setJobCount] = useState<number | null>(null)
  const autoHeadRef = useRef<HTMLHeadingElement | null>(null)
  const jobsHeadRef = useRef<HTMLHeadingElement | null>(null)
  const ids = useId()

  const load = useCallback(() => {
    listAutomations()
      .then((list) => {
        setAutomations(list)
        setSelected((cur) => (cur !== null && list.some((a) => a.id === cur) ? cur : (list[0]?.id ?? null)))
      })
      .catch((err: unknown) => setError(`자동화 목록을 불러오지 못했습니다: ${errorText(err)}`))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const current = automations?.find((a) => a.id === selected) ?? null
  return (
    <div className="saved-hub">
      <p className="upload-drop-hint">
        저장해 둔 설정을 한 곳에서 찾습니다. 통합 자동화는 이 화면에서 새 파일로 바로 실행하고, 공통 key 저장한 작업은
        &lsquo;적용하러 가기&rsquo;로 공통 key 연결 화면에 가서 이번 파일에 맞춰 씁니다. 두 종류는 따로 저장됩니다.
      </p>
      {/* 선택한 자동화의 실행 칸이 길어 아래 구역이 멀어지므로, 각 구역 제목으로 바로 옮기는 단추를 둔다 (제목은 초점만 받음) */}
      <nav className="saved-hub-jump" aria-label="이 화면의 구역">
        <button type="button" className="login-link" onClick={() => autoHeadRef.current?.focus()}>
          통합 자동화{automations !== null ? ` (${automations.length}개)` : ''}
        </button>
        <button type="button" className="login-link" onClick={() => jobsHeadRef.current?.focus()}>
          공통 key 저장한 작업{jobCount !== null ? ` (${jobCount}개)` : ''}
        </button>
      </nav>
      <section className="automation" aria-labelledby={`${ids}-auto`}>
        <h2 id={`${ids}-auto`} ref={autoHeadRef} tabIndex={-1}>
          통합 자동화{automations !== null ? ` (${automations.length}개)` : ''}
        </h2>
        <p className="upload-drop-hint">
          &lsquo;취합 작업&rsquo;에서 통합 결과를 만든 뒤 &lsquo;자동화로 저장&rsquo;하면 여기에 나타납니다. 새 파일을 올려 실행하면
          저장된 매핑 규칙·이름 연결·검증 조건·결과 열 순서를 그대로 적용하고, 저장된 규칙에 없는 새 열만 확인을 요청합니다.
        </p>
        {error && (
          <p className="upload-error" role="alert">
            {error}
          </p>
        )}
        {automations !== null && automations.length === 0 && (
          <p className="upload-drop-hint">
            저장된 통합 자동화가 없습니다. 공통 key 연결에서 저장한 작업은 아래 &lsquo;공통 key 저장한 작업&rsquo;에 있습니다.
          </p>
        )}
        {automations !== null && automations.length > 0 && (
          <ul className="automation-list">
            {automations.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  className={`automation-pick${a.id === selected ? ' is-selected' : ''}`}
                  aria-pressed={a.id === selected}
                  onClick={() => setSelected(a.id)}
                >
                  <span className="automation-pick-name">
                    <span className="kind-badge is-auto">통합 자동화</span>
                    <strong>{a.name}</strong>
                  </span>
                  <span>
                    열 규칙 {a.column_rules.length} · 이름 연결 {Object.keys(a.entity_rules).length} · 실행 {a.run_count}회
                    {a.owner_name && ` · ${a.owner_name}`}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {current && (
          <AutomationDetail
            key={current.id}
            automation={current}
            onChanged={load}
            onDeleted={() => {
              setSelected(null)
              load()
            }}
          />
        )}
      </section>
      <SavedKeyJoinJobs headRef={jobsHeadRef} onCount={setJobCount} />
    </div>
  )
}

type JobFocusTo = 'ask' | 'cancel' | 'sure' | 'recheck' | 'head'

/** 공통 key 저장한 작업 구역 — 목록(이름·버전·저장/수정 시각)·적용하러 가기·삭제.
 *  삭제는 공통 key 연결 화면(#77)과 같은 방식이다: 같은 자리의 확인(모달 아님 — 초점은 '취소', 이 확인 안의 Esc는 취소),
 *  대상은 확인을 연 때의 작업 번호로 고정, 요청이 실패하거나 응답을 받지 못하면 DELETE를 다시 보내지 않고 목록을 다시 읽어 결과를 정한다.
 *  지운 뒤에는 이 구역의 제목으로 초점이 가고 상태 알림이 남는다. */
function SavedKeyJoinJobs({
  headRef,
  onCount,
}: {
  headRef: RefObject<HTMLHeadingElement | null>
  onCount: (count: number | null) => void
}) {
  const ids = useId()
  const [jobs, setJobs] = useState<KeyJoinJob[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // 삭제 확인을 연 작업 — 번호·이름·판은 연 때의 것 (그 뒤 목록이 바뀌어도 대상이 바뀌지 않는다).
  // unknown: 삭제 결과를 확인하지 못함 (응답도 목록도 못 받음) — '정말 삭제' 대신 목록만 다시 읽는 '삭제 여부 다시 확인'
  const [delTarget, setDelTarget] = useState<{ id: number; name: string; version: number; unknown: boolean } | null>(null)
  const [focusReq, setFocusReq] = useState<{ to: JobFocusTo; id: number | null; n: number } | null>(null)
  const areaRef = useRef<HTMLElement | null>(null)
  const askRefs = useRef<Record<number, HTMLButtonElement | null>>({})
  const cancelRef = useRef<HTMLButtonElement | null>(null)
  const sureRef = useRef<HTMLButtonElement | null>(null)
  const recheckRef = useRef<HTMLButtonElement | null>(null)
  const deletingRef = useRef(false) // 다시 그리기 전에 두 번 눌려도 삭제 요청은 한 번
  const mountedRef = useRef(true) // 화면을 떠난 뒤 늦게 온 응답은 상태·초점을 건드리지 않는다
  const seq = useRef(0) // 목록 요청 순번 — 늦게 온 예전 목록이 더 새 목록(지운 결과)을 덮지 않게

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])
  useEffect(() => {
    onCount(jobs === null ? null : jobs.length)
  }, [jobs, onCount])
  useEffect(() => {
    const controller = new AbortController()
    const n = ++seq.current
    listKeyJoinJobs(controller.signal)
      .then((list) => {
        if (!controller.signal.aborted && n === seq.current) setJobs(list)
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted && n === seq.current) setError(`공통 key 저장한 작업 목록을 읽지 못했습니다: ${errorText(err)}`)
      })
    return () => controller.abort()
  }, [])
  useEffect(() => {
    if (!focusReq) return
    // 사용자가 그새 이 구역 밖으로 초점을 옮겼으면 빼앗지 않는다 (사라진 단추의 초점은 body로 떨어져 있다)
    const active = document.activeElement
    if (active && active !== document.body && !areaRef.current?.contains(active)) return
    const target =
      focusReq.to === 'ask'
        ? focusReq.id !== null
          ? askRefs.current[focusReq.id]
          : null
        : focusReq.to === 'cancel'
          ? cancelRef.current
          : focusReq.to === 'sure'
            ? sureRef.current
            : focusReq.to === 'recheck'
              ? recheckRef.current
              : headRef.current
    target?.focus()
  }, [focusReq, headRef])

  function requestFocus(to: JobFocusTo, id: number | null = null) {
    setFocusReq((f) => ({ to, id, n: (f?.n ?? 0) + 1 }))
  }

  function reload() {
    const n = ++seq.current
    listKeyJoinJobs()
      .then((list) => {
        if (mountedRef.current && n === seq.current) setJobs(list)
      })
      .catch((err: unknown) => {
        if (mountedRef.current && n === seq.current) setError(`공통 key 저장한 작업 목록을 읽지 못했습니다: ${errorText(err)}`)
      })
  }

  /** 인라인 삭제 확인: 누른 '삭제'는 사라지므로 덜 위험한 '취소'가 초점을 받는다. 대상은 누른 작업의 번호로 고정 */
  function openDelete(job: KeyJoinJob) {
    if (busy || deletingRef.current) return
    setError(null)
    setDelTarget({ id: job.id, name: job.name, version: job.version, unknown: false })
    requestFocus('cancel')
  }

  function cancelDelete() {
    if (busy) return // 요청을 보낸 뒤에는 취소할 수 없다 (결과를 기다린다)
    const id = delTarget?.id ?? null
    setDelTarget(null)
    requestFocus('ask', id)
  }

  /** 지운 것이 확인됨 — 남은 목록으로 바꾸고 안내·초점 (사라진 '정말 삭제' 대신 이 구역의 제목) */
  function showDeleted(rest: KeyJoinJob[], text: string) {
    setJobs(rest)
    setDelTarget(null)
    setNote(`${text} 실행 기록·결과 파일은 그대로입니다.${rest.length === 0 ? ' 남은 공통 key 저장한 작업이 없습니다.' : ''}`)
    requestFocus('head')
  }

  /** 삭제 응답이 실패·유실됐을 때(err) 또는 '삭제 여부 다시 확인'(err = null): DELETE는 다시 보내지 않고 목록만 다시 읽어
   *  그 작업이 서버에 남았는지로 결과를 정한다 — 서버가 지운 뒤 응답만 잃었거나 다른 창에서 먼저 지웠을(404) 수 있다 */
  async function settle(target: { id: number; name: string; version: number }, err: unknown) {
    seq.current += 1 // 이보다 먼저 보낸 목록 요청의 늦은 응답은 버린다
    const why = err === null ? '' : ` (삭제 응답: ${errorText(err)})`
    let list: KeyJoinJob[]
    try {
      list = await listKeyJoinJobs()
    } catch (readErr) {
      if (!mountedRef.current) return
      setDelTarget({ ...target, unknown: true })
      setError(
        `삭제 여부를 확인하지 못했습니다${why} — 목록도 읽지 못했습니다(${errorText(readErr)}). '삭제 여부 다시 확인'은 목록만 다시 읽습니다 (삭제 요청을 다시 보내지 않음).`,
      )
      requestFocus('recheck')
      return
    }
    if (!mountedRef.current) return
    if (list.some((j) => j.id === target.id)) {
      // 서버에 남아 있다 — 확인('정말 삭제'로 다시 시도·취소)은 그대로
      setJobs(list)
      setDelTarget({ ...target, unknown: false })
      setError(`지우지 못했습니다${why} — 목록을 다시 읽어 보니 작업은 그대로 있습니다. 다시 시도하거나 취소하세요.`)
      requestFocus('sure')
    } else {
      showDeleted(list, `'${target.name}' 작업은 지워져 목록에 없습니다 — 목록을 다시 읽어 확인했습니다${why}. 다른 창에서 먼저 지웠을 수도 있습니다.`)
    }
  }

  async function remove() {
    const target = delTarget
    if (target === null || target.unknown || busy || deletingRef.current) return
    deletingRef.current = true
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      try {
        await deleteKeyJoinJob(target.id)
      } catch (err) {
        if (mountedRef.current) await settle(target, err)
        return
      }
      if (!mountedRef.current) return
      showDeleted((jobs ?? []).filter((j) => j.id !== target.id), `공통 key 저장한 작업을 지웠습니다('${target.name}').`)
      reload()
    } finally {
      deletingRef.current = false
      if (mountedRef.current) setBusy(false)
    }
  }

  /** 결과를 확인하지 못한 삭제: 목록만 다시 읽는다 (DELETE를 다시 보내지 않음) */
  async function recheck() {
    const target = delTarget
    if (target === null || busy || deletingRef.current) return
    deletingRef.current = true
    setBusy(true)
    setError(null)
    try {
      await settle(target, null)
    } finally {
      deletingRef.current = false
      if (mountedRef.current) setBusy(false)
    }
  }

  const confirm = delTarget && (
    // 모달이 아닌 같은 자리의 확인 — 초점을 가두지 않고, 이 묶음 안에서만 Esc로 닫는다
    <div
      role="group"
      aria-labelledby={`${ids}-del`}
      className="kj-input saved-hub-confirm"
      onKeyDown={(e) => {
        if (e.key !== 'Escape' || busy) return
        e.preventDefault()
        e.stopPropagation()
        cancelDelete()
      }}
    >
      <p id={`${ids}-del`} className="kj-input-title">
        &lsquo;{delTarget.name}&rsquo; (v{delTarget.version}) 작업을 지울까요? 실행 기록·결과 파일은 남습니다 (Esc: 취소).
      </p>
      <div className="admin-actions kj-actions">
        {delTarget.unknown ? (
          <button ref={recheckRef} type="button" className="mapping-confirm" aria-disabled={busy} onClick={() => void recheck()}>
            {busy ? '확인 중…' : '삭제 여부 다시 확인'}
          </button>
        ) : (
          <button ref={sureRef} type="button" className="mapping-confirm" aria-disabled={busy} onClick={() => void remove()}>
            {busy ? '지우는 중…' : '정말 삭제 (실행 기록·결과는 남음)'}
          </button>
        )}
        <button ref={cancelRef} type="button" className="login-link" aria-disabled={busy} onClick={cancelDelete}>
          취소
        </button>
      </div>
    </div>
  )
  const confirmInList = delTarget !== null && (jobs ?? []).some((j) => j.id === delTarget.id)

  return (
    <section ref={areaRef} className="saved-hub-keyjoin" aria-labelledby={`${ids}-head`} aria-busy={busy}>
      {/* 지우면 사라진 단추 대신 이 제목이 초점을 받는다 (위 '구역' 단추도 여기로 옮긴다) */}
      <h2 id={`${ids}-head`} ref={headRef} tabIndex={-1}>
        공통 key 저장한 작업{jobs !== null ? ` (${jobs.length}개)` : ''}
      </h2>
      <p className="upload-drop-hint">
        &lsquo;공통 key로 연결하기&rsquo; 6단계의 &lsquo;저장한 작업으로 저장&rsquo;으로 만든 연결 설정입니다 (본인 것만 보입니다).
        &lsquo;적용하러 가기&rsquo;를 누르면 그 작업을 고른 채 공통 key 연결 화면으로 갑니다 — 쓸 파일은 그 화면의 안내대로 홈에서 고르고,
        이번 파일에 맞추기는 그 화면에서 합니다.
      </p>
      {jobs === null && !error && <p className="upload-drop-hint">목록을 읽는 중…</p>}
      {jobs !== null && jobs.length === 0 && (
        <p className="upload-drop-hint">
          저장한 공통 key 작업이 없습니다 — &lsquo;공통 key로 연결하기&rsquo; 6단계에서 저장할 수 있습니다.
        </p>
      )}
      {jobs !== null && jobs.length > 0 && (
        <ul className="bd-files saved-hub-jobs" aria-label="공통 key 저장한 작업 목록">
          {jobs.map((j) => (
            <li key={j.id}>
              <span className="kind-badge is-keyjoin">공통 key 작업</span>
              <strong>{j.name}</strong>
              <span>v{j.version}</span>
              <span>{jobTimeText(j)}</span>
              <span className="mw-muted">
                입력 {j.input_count}개 · key 열 {j.key_columns}개 · {j.scope_label}
              </span>
              <span className="saved-hub-actions">
                <NavLink href={`/keyjoin?job=${j.id}`}>
                  적용하러 가기<span className="sr-only"> — {j.name}</span>
                </NavLink>
                {delTarget?.id !== j.id && (
                  <button
                    ref={(el) => {
                      askRefs.current[j.id] = el
                    }}
                    type="button"
                    className="login-link"
                    aria-disabled={busy}
                    onClick={() => openDelete(j)}
                  >
                    삭제<span className="sr-only"> — {j.name}</span>
                  </button>
                )}
              </span>
              {delTarget?.id === j.id && confirm}
            </li>
          ))}
        </ul>
      )}
      {delTarget !== null && !confirmInList && confirm}
      <p role="status" className="mw-muted kj-jobnote">
        {note ?? ''}
      </p>
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}

function AutomationDetail({
  automation,
  onChanged,
  onDeleted,
}: {
  automation: Automation
  onChanged: () => void
  onDeleted: () => void
}) {
  const [files, setFiles] = useState<StoredFile[]>([])
  const [targets, setTargets] = useState<number[]>([])
  const [baseline, setBaseline] = useState<number[]>([])
  const [period, setPeriod] = useState(automation.validation.expected_period ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AutomationRunResult | null>(null)
  const [openReview, setOpenReview] = useState<number | null>(null)
  const [running, setRunning] = useState(false) // 자동화 실행 중 (업로드 중과 구분)
  const [askDelete, setAskDelete] = useState(false)

  const loadFiles = useCallback(() => {
    listFiles()
      .then(setFiles)
      .catch(() => undefined)
  }, [])
  useEffect(() => {
    loadFiles()
  }, [loadFiles])

  async function upload(list: FileList | null) {
    if (!list || list.length === 0) return
    setBusy(true)
    setError(null)
    try {
      const uploaded = await uploadFiles(Array.from(list))
      const ok = uploaded.filter((u) => u.id !== null && !u.error).map((u) => u.id as number)
      const failed = uploaded.filter((u) => u.id === null || u.error)
      if (failed.length) setError(`올리지 못한 파일: ${failed.map((f) => `${f.original_name} (${f.error})`).join(', ')}`)
      setTargets((prev) => [...new Set([...prev, ...ok])])
      setBaseline((prev) => prev.filter((id) => !ok.includes(id)))
      loadFiles()
    } catch (err) {
      setError(`업로드 실패: ${errorText(err)}`)
    } finally {
      setBusy(false)
    }
  }

  async function run() {
    setBusy(true)
    setRunning(true)
    setError(null)
    setResult(null)
    setOpenReview(null)
    try {
      const out = await runAutomation(automation.id, {
        file_ids: targets,
        baseline_file_ids: baseline,
        expected_period: period.trim() || null,
      })
      setResult(out)
      onChanged()
    } catch (err) {
      setError(outcomeText(err, '실행 실패: ')) // 결과를 모르는 응답은 '실패'로 쓰지 않는다
      onChanged() // 실행 기록·마지막 실행을 다시 읽는다
    } finally {
      setBusy(false)
      setRunning(false)
    }
  }

  function toggle(list: number[], id: number, on: boolean) {
    return on ? [...list, id] : list.filter((x) => x !== id)
  }

  const v = automation.validation
  return (
    <div className="automation-detail">
      <div className="automation-detail-head">
        <h3>{automation.name}</h3>
        <button
          type="button"
          className="mapping-confirm wk-danger"
          disabled={busy}
          onClick={() => setAskDelete(true)}
        >
          자동화 삭제
        </button>
      </div>
      <ConfirmDialog
        open={askDelete}
        title="자동화를 지울까요?"
        confirmLabel="자동화 삭제"
        danger
        onCancel={() => setAskDelete(false)}
        onConfirm={() => {
          setAskDelete(false)
          deleteAutomation(automation.id)
            .then(onDeleted)
            .catch((err: unknown) => setError(`삭제 실패: ${errorText(err)}`))
        }}
      >
        자동화 &lsquo;{automation.name}&rsquo;을(를) 지웁니다. 실행 기록과 결과 파일은 남습니다.
      </ConfirmDialog>
      <p className="upload-drop-hint">
        저장: {formatTime(automation.updated_at)} (실행 #{automation.source_run_id ?? '-'}에서) · 필수 기관{' '}
        {v.required_entities?.length ? v.required_entities.join(', ') : '없음'} · 증감 기준{' '}
        {Math.round((v.change_threshold ?? 0.5) * 100)}%
        {automation.last_run &&
          ` · 마지막 실행 #${automation.last_run.id} ${automation.last_run.status_label} (${formatDuration(automation.last_run.duration_ms)})`}
      </p>

      <details className="automation-rules">
        <summary>
          저장된 규칙 보기 (열 {automation.column_rules.length}개, 이름 연결 {Object.keys(automation.entity_rules).length}
          개, 결과 열 {automation.output_format.columns?.length ?? 0}개)
        </summary>
        <ul>
          {automation.column_rules.map((r) => (
            <li key={r.key}>
              {r.column} → {r.field_label ?? '(해당 없음)'}
            </li>
          ))}
          {Object.entries(automation.entity_rules).map(([name, code]) => (
            <li key={`e-${name}`}>
              {name} → {code ?? '(그대로 둠)'}
            </li>
          ))}
        </ul>
      </details>

      <div className="automation-run">
        <label className="automation-upload">
          새 파일 올리기
          <input
            type="file"
            multiple
            accept=".xlsx,.xls,.csv"
            disabled={busy}
            onChange={(e) => {
              void upload(e.target.files)
              e.target.value = ''
            }}
          />
        </label>
        <fieldset className="automation-files">
          <legend>실행할 파일 (이번 달 자료)</legend>
          {files.length === 0 && <p className="upload-drop-hint">업로드한 파일이 없습니다.</p>}
          {files.map((f) => (
            <label key={f.id}>
              <input
                type="checkbox"
                checked={targets.includes(f.id)}
                disabled={baseline.includes(f.id)}
                onChange={(e) => setTargets((prev) => toggle(prev, f.id, e.target.checked))}
              />
              #{f.id} {f.original_name}
            </label>
          ))}
        </fieldset>
        <details className="automation-files">
          <summary>전월 자료 (선택, 전월 대비 이상치 검사)</summary>
          {files.map((f) => (
            <label key={f.id}>
              <input
                type="checkbox"
                checked={baseline.includes(f.id)}
                disabled={targets.includes(f.id)}
                onChange={(e) => setBaseline((prev) => toggle(prev, f.id, e.target.checked))}
              />
              #{f.id} {f.original_name}
            </label>
          ))}
        </details>
        <label className="automation-period">
          기준 기간 (예: 2026-10, 비우면 검사 안 함)
          <input type="text" value={period} placeholder="YYYY-MM" onChange={(e) => setPeriod(e.target.value)} />
        </label>
        <button
          type="button"
          className="mapping-run"
          disabled={busy || targets.length === 0}
          onClick={() => void run()}
        >
          {busy ? '실행 중…' : `자동화 실행 (${targets.length}개 파일)`}
        </button>
        {!busy && targets.length === 0 && (
          <p className="wk-why" role="note">
            <strong>누를 수 없음</strong> 실행할 파일을 하나 이상 고르세요 (새로 올린 파일은 자동으로 골라집니다).
          </p>
        )}
      </div>

      {running && <ProgressPanel stages={REVIEW_STAGES} title="자동화 규칙 적용 중" />}
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {result && (
        <div className="automation-result" role="status">
          <p className="integration-title">
            실행 #{result.run.id}:{' '}
            <span className={`runs-status ${STATUS_CLASS[result.run.status] ?? ''}`}>{result.run.status_label}</span>{' '}
            {result.summary}
          </p>
          <p className="review-counts">
            <span className="review-chip is-pending">
              확인 필요 {result.counts.pending_open}/{result.counts.pending}
            </span>
            <span className="review-chip is-auto">자동 적용 {result.counts.auto}</span>
            <span className="review-chip is-block">차단 {result.counts.block}</span>
            <span className="review-chip">경고 {result.counts.warning}</span>
          </p>
          {result.run.status === 'done' && (
            <p>
              결과 파일: <code>{result.run.output_path}</code> · <RunDownloadLink runId={result.run.id}>내려받기</RunDownloadLink> ·
              소요 {formatDuration(result.run.duration_ms)}
            </p>
          )}
          {(result.run.status === 'failed' || result.run.status === 'check' || result.run.status === 'running') && (
            <p>
              <RunOutcomeText run={result.run} />
            </p>
          )}
          {result.run.status === 'review' && (
            <button type="button" className="mapping-confirm" onClick={() => setOpenReview(result.review_id)}>
              확인할 항목 검토하기
            </button>
          )}
        </div>
      )}
      {openReview !== null && (
        <ReviewPanel
          key={openReview}
          reviewId={openReview}
          onClose={() => {
            setOpenReview(null)
            onChanged()
          }}
        />
      )}
    </div>
  )
}

export default Automations
