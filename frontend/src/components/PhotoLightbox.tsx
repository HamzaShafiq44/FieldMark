import { X } from 'lucide-react'
import { type MouseEvent, useEffect, useRef } from 'react'

interface PhotoLightboxProps {
  src: string
  caption: string
  onClose: () => void
}

/** Full-size viewer for evidence photos, shared by the result panel and review drawer. */
function PhotoLightbox({ src, caption, onClose }: PhotoLightboxProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const previouslyFocusedRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    const active = document.activeElement
    previouslyFocusedRef.current = active instanceof HTMLElement ? active : null
    document.addEventListener('keydown', handleKeyDown)
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeButtonRef.current?.focus()
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = previousOverflow
      previouslyFocusedRef.current?.focus()
    }
  }, [onClose])

  const handleBackdropMouseDown = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) onClose()
  }

  return (
    <div className="photo-lightbox" onMouseDown={handleBackdropMouseDown}>
      <div
        className="photo-lightbox-inner"
        role="dialog"
        aria-modal="true"
        aria-label={caption}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <img src={src} alt={caption} />
        <div className="photo-lightbox-bar">
          <span>{caption}</span>
          <button
            ref={closeButtonRef}
            className="icon-button"
            type="button"
            onClick={onClose}
            aria-label="Close image"
          >
            <X size={18} />
          </button>
        </div>
      </div>
    </div>
  )
}

export default PhotoLightbox
