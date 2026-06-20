export function FernLogo({ size = 20 }: { size?: number }) {
  const height = size * 1.4
  return (
    <svg
      width={size}
      height={height}
      viewBox="0 0 24 34"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="Bracken"
    >
      <path d="M12 33 L12 2" stroke="var(--accent)" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M12 6 C9 5 7.5 3.5 7 1.5" stroke="var(--accent)" strokeWidth="1.2" strokeLinecap="round" fill="none" />
      <path d="M12 6 C15 5 16.5 3.5 17 1.5" stroke="var(--accent)" strokeWidth="1.2" strokeLinecap="round" fill="none" />
      <path d="M12 11 C8.5 9.5 6.5 7.5 5.5 5" stroke="var(--accent)" strokeWidth="1.2" strokeLinecap="round" fill="none" />
      <path d="M12 11 C15.5 9.5 17.5 7.5 18.5 5" stroke="var(--accent)" strokeWidth="1.2" strokeLinecap="round" fill="none" />
      <path d="M12 16 C8 14 5.5 11.5 4 8.5" stroke="var(--accent)" strokeWidth="1.2" strokeLinecap="round" fill="none" />
      <path d="M12 16 C16 14 18.5 11.5 20 8.5" stroke="var(--accent)" strokeWidth="1.2" strokeLinecap="round" fill="none" />
      <path d="M12 21 C7.5 19 4.5 16 2.5 12.5" stroke="var(--accent)" strokeWidth="1.2" strokeLinecap="round" fill="none" />
      <path d="M12 21 C16.5 19 19.5 16 21.5 12.5" stroke="var(--accent)" strokeWidth="1.2" strokeLinecap="round" fill="none" />
      <path d="M12 26 C7 24 3.5 20.5 1.5 16.5" stroke="var(--accent)" strokeWidth="1.2" strokeLinecap="round" fill="none" />
      <path d="M12 26 C17 24 20.5 20.5 22.5 16.5" stroke="var(--accent)" strokeWidth="1.2" strokeLinecap="round" fill="none" />
      <path d="M12 2 C11.5 1 11 0.5 10.5 0" stroke="var(--accent)" strokeWidth="1" strokeLinecap="round" fill="none" />
      <path d="M12 2 C12.5 1 13 0.5 13.5 0" stroke="var(--accent)" strokeWidth="1" strokeLinecap="round" fill="none" />
    </svg>
  )
}
