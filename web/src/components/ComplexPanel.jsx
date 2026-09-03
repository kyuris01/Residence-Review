import { useState } from 'react'
import AspectRow from './AspectRow.jsx'

function won(manwon) {
  if (!manwon) return null
  const eok = Math.floor(manwon / 10000)
  const rest = Math.round(manwon % 10000)
  if (eok > 0) return rest ? `${eok}억 ${rest.toLocaleString('ko-KR')}만원` : `${eok}억원`
  return `${Math.round(manwon).toLocaleString('ko-KR')}만원`
}

function ym(v) {
  if (!v || v.length < 6) return ''
  return `${v.slice(0, 4)}년 ${Number(v.slice(4, 6))}월`
}

export default function ComplexPanel({ complex, aspects, meta, onClose }) {
  const [openKey, setOpenKey] = useState(null)
  const info = complex.info || {}
  const rv = complex.review || { collected: 0, ad: 0, review: 0, evidence: 0 }
  const trade = info.recentTrade

  return (
    <aside className="panel" role="dialog" aria-label={`${complex.name} 상세`}>
      <button type="button" className="panel__close" onClick={onClose} aria-label="닫기">
        ✕
      </button>

      <header className="panel__head">
        <h2>{complex.name}</h2>
        <p className="panel__addr">{complex.roadAddr || complex.addr}</p>
        <dl className="facts">
          <div>
            <dt>준공</dt>
            <dd>{info.builtYear ? `${info.builtYear}년` : '-'}</dd>
          </div>
          <div>
            <dt>세대·동</dt>
            <dd>
              {info.houseCnt ? `${Math.round(info.houseCnt).toLocaleString('ko-KR')}세대` : '-'}
              {info.dongCnt ? ` · ${Math.round(info.dongCnt)}개동` : ''}
            </dd>
          </div>
          <div>
            <dt>난방</dt>
            <dd>{info.heatType || '-'}</dd>
          </div>
          <div>
            <dt>최근 실거래</dt>
            <dd>
              {trade
                ? `${won(trade.amount)} (${trade.area ? `${trade.area}㎡ · ` : ''}${ym(trade.ym)})`
                : '자료 없음'}
            </dd>
          </div>
        </dl>
      </header>

      <ul className="aspects">
        {aspects.map((a) => {
          const cell = complex.aspects[a.key]
          if (!cell) return null
          return (
            <AspectRow
              key={a.key}
              aspectKey={a.key}
              cell={cell}
              minEvidence={meta.minEvidence}
              expanded={openKey === a.key}
              onToggle={() => setOpenKey(openKey === a.key ? null : a.key)}
            />
          )
        })}
      </ul>

      <footer className="panel__foot">
        <p className="counts">
          수집 {rv.collected}건 · 광고 제외 {rv.ad}건 · 요약 근거 {rv.evidence}건
        </p>
        <p className="srcline">
          정량 출처: {meta.sources?.quant}
          {meta.sources?.feeBaseMonth ? ` (관리비 기준월 ${ym(meta.sources.feeBaseMonth)})` : ''}
        </p>
        <p className="srcline">
          판정 규칙: {meta.sources?.rule}
        </p>
        {meta.sources?.qual && <p className="srcline">정성 출처: {meta.sources.qual}</p>}
        <p className="disclaimer">{meta.disclaimer}</p>
      </footer>
    </aside>
  )
}
