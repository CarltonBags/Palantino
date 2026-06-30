interface Props {
  value: number | null;
  onRate: (n: number) => void;
}

export default function RatingBar({ value, onRate }: Props) {
  return (
    <div className="rating">
      <span className="rating-label">Bewertung</span>
      {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
        <button
          key={n}
          className={`rate-btn${value !== null && n <= value ? " on" : ""}`}
          onClick={() => onRate(n)}
          title={`${n}/10`}
        >
          {n}
        </button>
      ))}
    </div>
  );
}
