/** Convert a browser-recorded audio blob to a 16-bit PCM WAV blob. */

const WAV_HEADER_BYTES = 44
const PCM_BITS = 16
const PCM_FORMAT = 1

/** Write a little-endian ASCII tag into a DataView at the given offset. */
function writeAscii(view: DataView, offset: number, value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index))
  }
}

/** Encode an AudioBuffer into a 16-bit PCM WAV blob with proper duration metadata. */
export function audioBufferToWav(buffer: AudioBuffer): Blob {
  const channels = Math.min(buffer.numberOfChannels, 2)
  const sampleRate = buffer.sampleRate
  const frames = buffer.length
  const bytesPerSample = PCM_BITS / 8
  const blockAlign = channels * bytesPerSample
  const dataBytes = frames * blockAlign
  const arrayBuffer = new ArrayBuffer(WAV_HEADER_BYTES + dataBytes)
  const view = new DataView(arrayBuffer)

  writeAscii(view, 0, 'RIFF')
  view.setUint32(4, 36 + dataBytes, true)
  writeAscii(view, 8, 'WAVE')
  writeAscii(view, 12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, PCM_FORMAT, true)
  view.setUint16(22, channels, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * blockAlign, true)
  view.setUint16(32, blockAlign, true)
  view.setUint16(34, PCM_BITS, true)
  writeAscii(view, 36, 'data')
  view.setUint32(40, dataBytes, true)

  const channelData = Array.from({ length: channels }, (_, channel) =>
    buffer.getChannelData(channel),
  )
  let offset = WAV_HEADER_BYTES
  for (let frame = 0; frame < frames; frame += 1) {
    for (let channel = 0; channel < channels; channel += 1) {
      const sample = Math.max(-1, Math.min(1, channelData[channel][frame]))
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true)
      offset += bytesPerSample
    }
  }

  return new Blob([arrayBuffer], { type: 'audio/wav' })
}

/**
 * Decode a recorded blob and re-encode it as WAV so the browser player has
 * duration metadata and the transcription API gets a stable, widely-supported format.
 * Returns the original blob unchanged if decoding is not available or fails.
 */
export async function toPlayableWav(blob: Blob): Promise<Blob> {
  if (blob.type === 'audio/wav') return blob
  const AudioContextClass =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!AudioContextClass) return blob
  try {
    const context = new AudioContextClass()
    try {
      const decoded = await context.decodeAudioData(await blob.arrayBuffer())
      return audioBufferToWav(decoded)
    } finally {
      await context.close()
    }
  } catch {
    // If decoding fails, keep the original blob so the recording is not lost.
    return blob
  }
}

/** Peak amplitude below which a recording is treated as (near) silent. */
export const SILENCE_PEAK_THRESHOLD = 0.002

/** Result of preparing a browser recording for playback and transcription. */
export interface PreparedRecording {
  blob: Blob
  hasSpeech: boolean
  peak: number
}

/**
 * Decode a recording, measure whether it contains audible sound, and return a
 * playable WAV. A near-silent recording is flagged so the UI can warn the user,
 * but it is still passed on so the transcription API can make the final call.
 */
export async function prepareRecording(blob: Blob): Promise<PreparedRecording> {
  const AudioContextClass =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!AudioContextClass) {
    // Cannot analyze without Web Audio; trust that a non-trivial recording has speech.
    return { blob, hasSpeech: blob.size > 512, peak: 1 }
  }

  const context = new AudioContextClass()
  try {
    const decoded = await context.decodeAudioData(await blob.arrayBuffer())
    const wav = audioBufferToWav(decoded)

    // Sample the waveform for any audible signal above a very low noise floor.
    let peak = 0
    for (let channel = 0; channel < Math.min(decoded.numberOfChannels, 2); channel += 1) {
      const samples = decoded.getChannelData(channel)
      // Check at most ~1s of samples spread across the clip for efficiency.
      const step = Math.max(1, Math.floor(samples.length / 16000))
      for (let index = 0; index < samples.length; index += step) {
        const amplitude = Math.abs(samples[index])
        if (amplitude > peak) peak = amplitude
      }
    }
    return { blob: wav, hasSpeech: peak > SILENCE_PEAK_THRESHOLD, peak }
  } finally {
    await context.close()
  }
}

declare global {
  interface Window {
    webkitAudioContext?: typeof AudioContext
  }
}
