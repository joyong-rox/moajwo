import { useEffect, useState, type FormEvent } from 'react'
import { fetchLlmSettings, saveLlmSettings, testLlmSettings, type LlmMode, type LlmSettings as Data } from './api'
import { formatTime } from './runFormat'

const MODE_HINT: Record<LlmMode, string> = {
  ollama: '이 PC(서버)의 Ollama로 판정합니다. 자료가 밖으로 나가지 않습니다 (폐쇄망 기본값).',
  openai: 'OpenAI API로 판정합니다. API Key와 모델 이름이 필요합니다.',
  anthropic: 'Anthropic API로 판정합니다. API Key가 필요합니다 (모델은 비우면 기본값).',
  custom: 'OpenAI 호환 /chat/completions 엔드포인트(사내 vLLM·LM Studio 등)로 판정합니다.',
}
const MODES: LlmMode[] = ['ollama', 'openai', 'anthropic', 'custom']

/** 설정 > LLM 제공자 (소유자). 열 매핑 3단계 판정에 쓸 LLM을 고른다. */
function LlmSettings() {
  const [data, setData] = useState<Data | null>(null)
  const [mode, setMode] = useState<LlmMode>('ollama')
  const [model, setModel] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  function apply(d: Data) {
    setData(d)
    setMode(d.mode)
    setModel(d.model ?? '')
    setBaseUrl(d.base_url ?? '')
    setApiKey('')
  }

  useEffect(() => {
    fetchLlmSettings()
      .then(apply)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  const external = mode === 'openai' || mode === 'anthropic' || mode === 'custom'
  const sameMode = data?.mode === mode
  const keyField = mode !== 'ollama'

  async function save(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const saved = await saveLlmSettings({
        mode,
        model: model.trim() || null,
        base_url: baseUrl.trim() || null,
        // 같은 제공자에서 키를 비워 두면 저장된 키를 유지한다
        ...(apiKey.trim() || !sameMode ? { api_key: apiKey.trim() } : {}),
      })
      apply(saved)
      setMessage(`저장했습니다 — 이제 '${saved.mode_label}'로 열 매핑을 판정합니다.`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function test() {
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const r = await testLlmSettings()
      const text = `${r.mode_label} · ${r.model ?? '-'} · ${r.latency_ms.toLocaleString('ko-KR')}ms — ${r.message}`
      if (r.ok) setMessage(`연결 확인: ${text}`)
      else setError(`연결 실패: ${text}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="admin" aria-label="LLM 제공자 설정">
      <h2>LLM 제공자 설정</h2>
      <p className="upload-drop-hint">
        열 매핑 3단계(사전·유사도로 정하지 못한 열)의 판정에 쓸 LLM입니다. 임베딩(유사도)은 모드와 관계없이 항상 로컬
        Ollama를 씁니다.
      </p>
      {data && (
        <p className="llm-current">
          현재: <strong>{data.mode_label}</strong> · 모델 {data.effective_model ?? '-'}
          {data.is_external && <span className="llm-external-tag">외부 전송</span>}
          {data.updated_at && ` · ${data.updated_by_name ?? '-'} ${formatTime(data.updated_at)} 변경`}
        </p>
      )}
      <form className="llm-form" onSubmit={(e) => void save(e)}>
        <fieldset className="llm-modes">
          <legend>제공자</legend>
          {MODES.map((m) => (
            <label key={m} className={`llm-mode${mode === m ? ' is-selected' : ''}`}>
              <input
                type="radio"
                name="llm-mode"
                value={m}
                checked={mode === m}
                onChange={() => {
                  setMode(m)
                  setApiKey('')
                  if (m !== data?.mode) {
                    setModel('')
                    setBaseUrl('')
                  } else if (data) {
                    setModel(data.model ?? '')
                    setBaseUrl(data.base_url ?? '')
                  }
                }}
              />
              <span className="llm-mode-text">
                <strong>
                  {data?.modes[m] ?? m}
                  {m === 'ollama' && ' · 기본'}
                </strong>
                <span>{MODE_HINT[m]}</span>
              </span>
            </label>
          ))}
        </fieldset>

        {external && (
          <div className="llm-warning" role="note">
            <strong>외부로 전송되는 내용</strong> — 판정이 필요한 열의 이름, 그 열의 샘플 값 최대 5개(주민번호·전화·이메일
            패턴은 마스킹된 값), 표준 항목 목록. 원본 파일 전체와 결과 파일은 보내지 않습니다. 폐쇄망 원칙에서 벗어나므로
            기관의 보안 지침을 확인한 뒤 사용하세요.
          </div>
        )}

        <div className="admin-form">
          <label>
            모델 이름{mode === 'openai' || mode === 'custom' ? ' (필수)' : ' (비우면 기본값)'}
            <input
              id="llm-model"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder={data?.default_models[mode] ?? (mode === 'openai' ? '예: gpt-4.1-mini' : '예: qwen2.5-7b-instruct')}
              maxLength={200}
            />
          </label>
          {mode !== 'ollama' && (
            <label>
              엔드포인트 URL{mode === 'custom' ? ' (필수)' : ' (비우면 공식 주소)'}
              <input
                id="llm-base-url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={mode === 'custom' ? 'http://10.0.0.5:8000/v1' : mode === 'openai' ? 'https://api.openai.com/v1' : 'https://api.anthropic.com'}
                maxLength={500}
              />
            </label>
          )}
          {keyField && (
            <label>
              API Key{mode === 'custom' ? ' (선택)' : ' (필수)'}
              <input
                id="llm-api-key"
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={sameMode && data?.api_key_set ? `저장됨 ${data.api_key_hint ?? ''} — 바꿀 때만 입력` : '키 입력'}
                maxLength={500}
              />
            </label>
          )}
        </div>
        <p className="upload-drop-hint">
          API Key는 서버에만 저장되며 화면·응답에 다시 표시하지 않습니다 (끝 4자리만). 로컬로 되돌리면 저장된 키를 지웁니다.
        </p>
        <div className="review-actions">
          <button type="submit" className="mapping-run" disabled={busy}>
            저장
          </button>
          <button type="button" className="mapping-confirm" disabled={busy || !sameMode} onClick={() => void test()}>
            {busy ? '확인 중…' : '연결 테스트 (저장된 설정)'}
          </button>
        </div>
        {!busy && !sameMode && data && (
          <p className="wk-why" role="note">
            <strong>연결 테스트를 누를 수 없음</strong> 고른 방식이 저장된 설정과 다릅니다 — 먼저 저장하세요.
          </p>
        )}
      </form>
      {message && (
        <p className="mapping-message" role="status">
          {message}
        </p>
      )}
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}

export default LlmSettings
