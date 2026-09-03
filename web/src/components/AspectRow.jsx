const SOURCE_LABEL = {
  naver_cafe: '네이버 카페',
  daum_cafe: '다음 카페',
  naver_blog: '네이버 블로그',
  blog: '블로그',
  web: '웹문서',
  review_platform: '리뷰 플랫폼',
  youtube_comment: '유튜브 댓글',
}

const VERDICT_LABEL = { pro: '장점', con: '단점' }

/** 항목별 표기 단위에 맞춰 수치를 문자열로 */
export function formatValue(key, v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '-'
  const n = Number(v)
  if (key === 'parking') return `${n.toFixed(2)}대/세대`
  if (key === 'fee') return `${Math.round(n).toLocaleString('ko-KR')}원/㎡`
  if (key === 'security') return `경비원 1명당 ${Math.round(n).toLocaleString('ko-KR')}세대`
  if (key === 'scale') return `${Math.round(n).toLocaleString('ko-KR')}세대`
  if (key === 'age') return `${Math.round(n)}년 준공`
  return `${Math.round(n)}개 항목`
}

export default function AspectRow({ aspectKey, cell, minEvidence, expanded, onToggle }) {
  const qual = cell.qual
  const hasCard = cell.verdict === 'pro' || cell.verdict === 'con'
  const noValue = cell.value === null || cell.value === undefined
  const sources = (qual && qual.sources) || []

  return (
    <li className={`aspect aspect--${cell.verdict}`}>
      <div className="aspect__head">
        <span className="aspect__label">{cell.label}</span>
        {hasCard ? (
          <span className={`badge badge--${cell.verdict}`}>{VERDICT_LABEL[cell.verdict]}</span>
        ) : (
          <span className="badge badge--none">{noValue ? '자료 없음' : '중간'}</span>
        )}
        <span className="aspect__value">{formatValue(aspectKey, cell.value)}</span>
      </div>

      {/* 정량 근거 — 규칙으로 계산, LLM 미사용 */}
      <p className="aspect__quant">
        <span className="tag tag--quant">정량</span>
        {cell.quantText || '판정 없음'}
      </p>

      {/* 정성 근거 — 후기 요약. 근거 minEvidence 건 미만이면 만들지 않는다 */}
      {qual ? (
        <p className="aspect__qual">
          <span className={`tag tag--qual tag--${qual.polarity}`}>후기</span>
          {qual.text}
          <span className="aspect__count">근거 {qual.evidenceCount}건</span>
        </p>
      ) : (
        <p className="aspect__qual aspect__qual--empty">
          <span className="tag tag--muted">후기</span>
          유효 후기 {minEvidence}건 미만으로 요약을 만들지 않았습니다
        </p>
      )}

      {/* 사분위 기준 표기 — 판정 근거를 숨기지 않는다 */}
      {cell.dongAvg !== undefined && (
        <p className="aspect__stat">
          동 평균 {formatValue(aspectKey, cell.dongAvg)} · 하위 25% 경계{' '}
          {formatValue(aspectKey, cell.q1)} · 상위 25% 경계 {formatValue(aspectKey, cell.q3)}
          {cell.rank ? ` · ${cell.n}개 단지 중 ${cell.rank}위` : ''}
        </p>
      )}

      {sources.length > 0 && (
        <>
          <button type="button" className="aspect__more" onClick={onToggle}>
            {expanded ? '근거 접기' : `근거가 된 글 ${sources.length}건 보기`}
          </button>
          {expanded && (
            <ul className="sources">
              {sources.map((s, i) => (
                <li key={`${s.url}-${i}`}>
                  <span className="sources__type">{SOURCE_LABEL[s.source] || s.source}</span>
                  <a href={s.url} target="_blank" rel="noreferrer noopener">
                    {s.title}
                  </a>
                </li>
              ))}
              <li className="sources__note">
                원문 본문과 댓글 원문은 표시하지 않습니다. 확인은 링크로 이동하세요.
              </li>
            </ul>
          )}
        </>
      )}
    </li>
  )
}
