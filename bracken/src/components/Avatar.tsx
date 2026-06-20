import { useEffect, useRef } from 'react'

interface Props {
  value: string
  size?: number
  className?: string
}

export function Avatar({ value, size = 48, className }: Props) {
  const ref = useRef<SVGSVGElement>(null)

  useEffect(() => {
    if (ref.current && typeof window !== 'undefined' && 'jdenticon' in window) {
      const jd = (window as unknown as { jdenticon: { update: (el: SVGElement) => void } }).jdenticon
      jd.update(ref.current)
    }
  }, [value])

  return (
    <svg
      ref={ref}
      data-jdenticon-value={value}
      width={size}
      height={size}
      className={className}
    />
  )
}
