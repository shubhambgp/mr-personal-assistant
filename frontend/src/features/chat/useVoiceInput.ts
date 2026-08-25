// Dictation for the composer, Apple-style: tap the mic, speak, watch the text
// appear — with a live level meter so the rep can see the mic is hearing them.
//
// Two browser APIs, deliberately together:
//   SpeechRecognition — the words. Built into Chrome/Edge/Safari; free; no
//     audio ever touches our backend.
//   getUserMedia + AnalyserNode — the meter. Recognition exposes no levels, so
//     a second capture drives the visualisation (browsers allow both at once).
//
// TypeScript's DOM lib does not type SpeechRecognition, so minimal structural
// types are declared here — no `any`, per the repo's strict rule.

import { useCallback, useEffect, useRef, useState } from 'react'

interface SpeechAlternativeLike {
  transcript: string
}
interface SpeechResultLike {
  isFinal: boolean
  0: SpeechAlternativeLike
}
interface SpeechResultListLike {
  readonly length: number
  [index: number]: SpeechResultLike
}
interface SpeechEventLike {
  results: SpeechResultListLike
}
interface SpeechErrorEventLike {
  error: string
}
interface SpeechRecognitionLike {
  lang: string
  continuous: boolean
  interimResults: boolean
  onresult: ((event: SpeechEventLike) => void) | null
  onend: (() => void) | null
  onerror: ((event: SpeechErrorEventLike) => void) | null
  start(): void
  stop(): void
  abort(): void
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecognitionCtor
    webkitSpeechRecognition?: SpeechRecognitionCtor
  }
}

function recognitionCtor(): SpeechRecognitionCtor | null {
  return window.SpeechRecognition ?? window.webkitSpeechRecognition ?? null
}

/** Human sentences for the error codes a rep can actually act on. */
function explain(code: string): string {
  switch (code) {
    case 'not-allowed':
    case 'service-not-allowed':
      return 'Microphone access was blocked. Allow it in your browser to dictate.'
    case 'audio-capture':
      return 'No microphone was found.'
    case 'network':
      return 'Speech recognition needs a network connection.'
    case 'no-speech':
      return '' // silence is not an error worth a banner
    default:
      return 'Dictation stopped unexpectedly. Tap the mic to try again.'
  }
}

export function useVoiceInput(
  /** Called with (finalised text, in-progress text) as the rep speaks. The
   *  caller owns how it lands in the input. */
  onTranscript: (finalText: string, interim: string) => void,
) {
  const [listening, setListening] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const onTranscriptRef = useRef(onTranscript)
  // Kept fresh in an effect, not during render (react-hooks/refs): recognition
  // handlers outlive any single render and must call the latest callback.
  useEffect(() => {
    onTranscriptRef.current = onTranscript
  }, [onTranscript])

  const supported = typeof window !== 'undefined' && recognitionCtor() !== null

  const teardownAudio = useCallback(() => {
    streamRef.current?.getTracks()?.forEach((track) => track.stop())
    streamRef.current = null
    void audioCtxRef.current?.close().catch(() => undefined)
    audioCtxRef.current = null
    analyserRef.current = null
  }, [])

  const stop = useCallback(() => {
    // Graceful stop: recognition finalises the last phrase and fires onend,
    // which is where listening flips false and the meter is torn down.
    recognitionRef.current?.stop()
  }, [])

  const start = useCallback(() => {
    const Ctor = recognitionCtor()
    if (!Ctor || recognitionRef.current) return
    setError(null)

    const recognition = new Ctor()
    recognition.lang = navigator.language || 'en-IN'
    recognition.continuous = true
    recognition.interimResults = true

    recognition.onresult = (event) => {
      // Rebuilt from scratch each event rather than appended: a continuous
      // session revises earlier interim results, and append-only dictation
      // duplicates every revised phrase.
      let finalText = ''
      let interim = ''
      for (let i = 0; i < event.results.length; i += 1) {
        const result = event.results[i]
        if (!result) continue
        const text = result[0].transcript
        if (result.isFinal) finalText += text
        else interim += text
      }
      onTranscriptRef.current(finalText, interim)
    }
    recognition.onerror = (event) => {
      const message = explain(event.error)
      if (message) setError(message)
    }
    recognition.onend = () => {
      recognitionRef.current = null
      setListening(false)
      teardownAudio()
    }

    recognitionRef.current = recognition
    recognition.start()
    setListening(true)

    // The level meter — best-effort: dictation still works if this is denied
    // or the AudioContext cannot start.
    void (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        // The rep may have stopped dictating before permission resolved.
        if (!recognitionRef.current) {
          stream.getTracks()?.forEach((track) => track.stop())
          return
        }
        const ctx = new AudioContext()
        const analyser = ctx.createAnalyser()
        analyser.fftSize = 128
        analyser.smoothingTimeConstant = 0.7
        ctx.createMediaStreamSource(stream).connect(analyser)
        streamRef.current = stream
        audioCtxRef.current = ctx
        analyserRef.current = analyser
      } catch {
        /* meter only — dictation carries on without it */
      }
    })()
  }, [teardownAudio])

  // Sign-out or a conversation switch mid-sentence must not leave the mic on.
  useEffect(
    () => () => {
      recognitionRef.current?.abort()
      recognitionRef.current = null
      teardownAudio()
    },
    [teardownAudio],
  )

  return { supported, listening, error, start, stop, analyserRef }
}
