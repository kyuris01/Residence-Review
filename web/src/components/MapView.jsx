import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'

/**
 * OpenStreetMap(Leaflet) 지도.
 * 지도 API 키가 필요 없다 — 타일은 OSM 공개 타일 서버를 쓰고 저작자 표시만 지킨다.
 */
const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> 기여자'

/** 근거 상태 — 정성 요약이 하나라도 있으면 'both', 아니면 'quant' */
export function evidenceLevel(complex) {
  const hasQual = Object.values(complex.aspects || {}).some((a) => a && a.qual)
  return hasQual ? 'both' : 'quant'
}

function makePin(complex, onSelect) {
  const el = document.createElement('button')
  el.type = 'button'
  el.className = `pin pin--${evidenceLevel(complex)}`
  const dot = document.createElement('span')
  dot.className = 'pin__dot'
  const name = document.createElement('span')
  name.className = 'pin__name'
  name.textContent = complex.name // textContent 라 이름에 특수문자가 있어도 안전하다
  el.append(dot, name)
  el.addEventListener('click', (e) => {
    e.stopPropagation()
    onSelect(complex.kaptCode)
  })
  return el
}

export default function MapView({ complexes, selectedCode, onSelect, center, zoom, onError }) {
  const boxRef = useRef(null)
  const mapRef = useRef(null)
  const pinsRef = useRef(new Map())
  const [ready, setReady] = useState(false)

  // --- 지도 + 마커 생성 (최초 1회) ---
  useEffect(() => {
    if (!boxRef.current || mapRef.current) return
    try {
      // config 의 줌은 Leaflet 기준(클수록 확대). 예전 카카오 레벨 값이 남아 있으면 보정한다.
      const initialZoom = !zoom || zoom < 10 ? 14 : zoom
      const map = L.map(boxRef.current, {
        center: [center.lat, center.lng],
        zoom: initialZoom,
        // 기본 위치(좌상단/우하단)는 상단 카드·범례와 겹쳐서 좌하단으로 모아둔다
        zoomControl: false,
        attributionControl: false,
      })
      L.tileLayer(TILE_URL, { maxZoom: 19, attribution: TILE_ATTRIBUTION }).addTo(map)
      L.control.zoom({ position: 'bottomleft' }).addTo(map)
      L.control
        .attribution({
          position: 'bottomleft',
          prefix: '<a href="https://leafletjs.com" target="_blank" rel="noreferrer">Leaflet</a>',
        })
        .addTo(map)
      mapRef.current = map

      const points = []
      complexes.forEach((c) => {
        if (!c.lat || !c.lng) return // 좌표 없는 단지는 범례에서 따로 안내한다
        const el = makePin(c, onSelect)
        const marker = L.marker([c.lat, c.lng], {
          icon: L.divIcon({ html: el, className: 'pin-icon', iconSize: [0, 0] }),
          keyboard: false,
          // 빛가람동은 단지가 촘촘해 이름표가 서로 겹친다. 겹친 상태에서는 위에
          // 깔린 마커가 클릭을 가로채므로, 마우스를 올린 마커를 맨 앞으로 올린다.
          riseOnHover: true,
          riseOffset: 1000,
        }).addTo(map)
        pinsRef.current.set(c.kaptCode, el)
        points.push([c.lat, c.lng])
        marker.on('click', () => onSelect(c.kaptCode))
      })

      if (points.length > 1) map.fitBounds(points, { padding: [70, 70], maxZoom: 16 })
      else if (points.length === 1) map.setView(points[0], 16)

      map.on('click', () => onSelect(null))
      setReady(true)
    } catch (err) {
      onError(err.message || '지도를 초기화하지 못했습니다')
    }
    // 지도는 한 번만 만든다 (데이터는 정적 번들이라 도중에 바뀌지 않는다)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // --- 선택 상태에 따라 마커 강조 + 지도 이동 ---
  useEffect(() => {
    if (!ready) return
    pinsRef.current.forEach((el, code) => {
      el.classList.toggle('pin--active', code === selectedCode)
    })
    const hit = complexes.find((c) => c.kaptCode === selectedCode)
    if (hit && hit.lat && mapRef.current) {
      mapRef.current.panTo([hit.lat, hit.lng])
    }
  }, [selectedCode, ready, complexes])

  return <div className="map" ref={boxRef} />
}
