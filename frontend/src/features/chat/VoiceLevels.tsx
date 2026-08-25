// The voice modulation meter beside the mic: five bars that move with the
// rep's actual voice, so "is it hearing me?" is answered by looking, the way
// Apple's dictation pill does it.
//
// Driven by requestAnimationFrame writing transforms straight to the DOM —
// zero React re-renders at 60fps, and `transform: scaleY` never causes layout.

import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'

const BAR_COUNT = 5
//: Idle baseline so the meter reads as "on and waiting" during silence rather
//: than looking dead.
const MIN_SCALE = 0.25

export function VoiceLevels({ analyserRef }: { analyserRef: RefObject<AnalyserNode | null> }) {
  const barsRef = useRef<(HTMLSpanElement | null)[]>([])

  useEffect(() => {
    let frame = 0
    let buffer: Uint8Array<ArrayBuffer> | null = null

    const tick = () => {
      frame = requestAnimationFrame(tick)
      const analyser = analyserRef.current
      if (!analyser) {
        // Permission still resolving: gentle idle pulse so the meter is alive.
        const t = performance.now() / 300
        barsRef.current?.forEach((bar, i) => {
          if (bar) bar.style.transform = `scaleY(${MIN_SCALE + 0.08 * Math.abs(Math.sin(t + i))})`
        })
        return
      }
      if (!buffer || buffer.length !== analyser.frequencyBinCount) {
        buffer = new Uint8Array(analyser.frequencyBinCount)
      }
      analyser.getByteFrequencyData(buffer)
      // Voice lives in the lower bins; split them into one band per bar.
      const usable = Math.max(BAR_COUNT, Math.floor(buffer.length * 0.6))
      const bandSize = Math.floor(usable / BAR_COUNT)
      for (let i = 0; i < BAR_COUNT; i += 1) {
        let sum = 0
        for (let j = i * bandSize; j < (i + 1) * bandSize; j += 1) sum += buffer[j] ?? 0
        const level = sum / bandSize / 255
        const bar = barsRef.current[i]
        if (bar) bar.style.transform = `scaleY(${MIN_SCALE + level * (1 - MIN_SCALE)})`
      }
    }

    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [analyserRef])

  return (
    <span className="flex h-6 items-center gap-0.5 px-1" role="img" aria-label="Microphone level">
      {Array.from({ length: BAR_COUNT }, (_, i) => (
        <span
          key={i}
          ref={(node) => {
            barsRef.current[i] = node
          }}
          className="h-4 w-0.5 origin-center rounded-full bg-accent"
          style={{ transform: `scaleY(${MIN_SCALE})` }}
        />
      ))}
    </span>
  )
}
