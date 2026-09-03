import { useEffect, useState } from 'react'
import MapView, { evidenceLevel } from './components/MapView.jsx'
import ComplexPanel from './components/ComplexPanel.jsx'
import Legend from './components/Legend.jsx'

function formatDate(iso) {
  if (!iso) return ''
  return iso.slice(0, 10).replace(/-/g, '.')
}

export default function App() {
  const [data, setData] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [mapError, setMapError] = useState(null)
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}data.json`)
      .then((res) => {
        if (!res.ok) throw new Error(`data.json 을 불러오지 못했습니다 (HTTP ${res.status})`)
        return res.json()
      })
      .then(setData)
      .catch((err) => setLoadError(err.message))
  }, [])

  if (loadError) {
    return (
      <div className="boot boot--error">
        <h1>데이터를 불러오지 못했습니다</h1>
        <p>{loadError}</p>
        <p className="boot__hint">
          <code>python scripts/make_mock.py</code> 또는 배치 스크립트 ①②③ 을 실행해
          <code> web/public/data.json</code> 을 만들어 주세요.
        </p>
      </div>
    )
  }
  if (!data) return <div className="boot">불러오는 중…</div>

  const complexes = data.complexes || []
  const selectedComplex = complexes.find((c) => c.kaptCode === selected) || null
  const counts = {
    total: complexes.length,
    both: complexes.filter((c) => evidenceLevel(c) === 'both').length,
  }
  const meta = {
    minEvidence: data.minEvidence || 3,
    disclaimer: data.disclaimer,
    sources: data.sources || {},
  }

  return (
    <div className={`app${selectedComplex ? ' has-panel' : ''}`}>
      {data.isMock && (
        <div className="mockbar">
          데모 데이터입니다 — 실제 단지·수치가 아닙니다. 배치 스크립트를 실행하면 실제
          데이터로 바뀝니다.
        </div>
      )}

      <header className="topbar">
        <h1>빛가람 아파트 리뷰·데이터 요약 지도</h1>
        <p>
          대상 지역 {data.region?.sido} {data.region?.sigungu} {data.region?.dong} · 데이터 기준일{' '}
          {formatDate(data.generatedAt)}
        </p>
      </header>

      {mapError ? (
        <div className="fallback">
          <div className="fallback__msg">
            <strong>지도를 표시할 수 없습니다.</strong> {mapError}
            <br />
            지도 타일(OpenStreetMap)에 연결하지 못했을 수 있습니다. 아래 목록에서 단지를
            선택하면 지도와 같은 패널이 열립니다.
          </div>
          <ul className="fallback__list">
            {complexes.map((c) => (
              <li key={c.kaptCode}>
                <button
                  type="button"
                  className={`fallback__item fallback__item--${evidenceLevel(c)} ${
                    selected === c.kaptCode ? 'is-active' : ''
                  }`}
                  onClick={() => setSelected(c.kaptCode)}
                >
                  <span className="dot" />
                  <span className="fallback__name">{c.name}</span>
                  <span className="fallback__sub">
                    {c.info?.builtYear ? `${c.info.builtYear}년` : ''}
                    {c.info?.houseCnt
                      ? ` · ${Math.round(c.info.houseCnt).toLocaleString('ko-KR')}세대`
                      : ''}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <MapView
          complexes={complexes}
          selectedCode={selected}
          onSelect={setSelected}
          center={data.region?.center || { lat: 35.0208, lng: 126.79 }}
          zoom={data.region?.zoom || 5}
          onError={setMapError}
        />
      )}

      {selectedComplex && (
        <ComplexPanel
          complex={selectedComplex}
          aspects={data.aspects || []}
          meta={meta}
          onClose={() => setSelected(null)}
        />
      )}

      <Legend
        counts={counts}
        disclaimer={data.disclaimer}
        dongSummary={data.dongSummary}
        unlocated={mapError ? [] : complexes.filter((c) => !c.lat || !c.lng)}
        onSelect={setSelected}
      />
    </div>
  )
}
