import { useState } from 'react'

export default function Legend({ counts, disclaimer, dongSummary, unlocated = [], onSelect }) {
  const [open, setOpen] = useState(null) // 'note' | 'dong' | 'unlocated' | null

  return (
    <div className="legend">
      {open === 'note' && (
        <div className="legend__pop">
          <h3>면책</h3>
          <p>{disclaimer}</p>
          <p>
            정량 지표는 공공데이터를 규칙으로 계산한 값이며, 정성 요약은 공개된 글·댓글을 AI가
            요약한 것입니다. 원문 본문은 표시하지 않으며 확인은 원문 링크로 이동해 주세요.
          </p>
          <p>지도 · 지도 데이터 © OpenStreetMap 기여자 (ODbL).</p>
        </div>
      )}

      {open === 'dong' && dongSummary && (
        <div className="legend__pop">
          <h3>동 전체 이야기</h3>
          <p className="legend__sub">
            단지를 특정할 수 없는 댓글 {dongSummary.collected}건에서 뽑은 지역 공통 언급입니다.
          </p>
          <ul>
            {dongSummary.items.map((it) => (
              <li key={it.key}>
                <strong>{it.label}</strong> — {it.text}{' '}
                <span className="legend__count">근거 {it.evidenceCount}건</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {open === 'unlocated' && unlocated.length > 0 && (
        <div className="legend__pop">
          <h3>좌표 미확보 단지</h3>
          <p className="legend__sub">
            지오코딩에서 위치를 찾지 못해 지도에 찍히지 않았습니다. 눌러서 패널을 열 수 있습니다.
          </p>
          <ul className="legend__unlocated">
            {unlocated.map((c) => (
              <li key={c.kaptCode}>
                <button type="button" onClick={() => onSelect(c.kaptCode)}>
                  {c.name}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="legend__box">
        <p className="legend__row">
          <span className="dot dot--both" /> 후기 + 정량 근거
        </p>
        <p className="legend__row">
          <span className="dot dot--quant" /> 정량 근거만
        </p>
        <p className="legend__meta">
          단지 {counts.total}개 · 후기 있는 단지 {counts.both}개
        </p>
        <div className="legend__links">
          {dongSummary && (
            <button type="button" onClick={() => setOpen(open === 'dong' ? null : 'dong')}>
              동 전체 이야기
            </button>
          )}
          {unlocated.length > 0 && (
            <button
              type="button"
              onClick={() => setOpen(open === 'unlocated' ? null : 'unlocated')}
            >
              좌표 없음 {unlocated.length}
            </button>
          )}
          <button type="button" onClick={() => setOpen(open === 'note' ? null : 'note')}>
            면책 문구
          </button>
        </div>
      </div>
    </div>
  )
}
