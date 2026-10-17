import { Icon, type IconName } from ".";
const GAMES: [string, IconName][] = [
  ["Vault lights", "key"],
  ["Spy codewords", "search"],
  ["Crack the machine", "gears"],
  ["Bet your coins", "coin"],
  ["The locked box", "box"],
];
/** Preview cards, not fake navigation: the sitting itself is server-sequenced. */
export function GameCards() {
  return (
    <details className="mt-4">
      <summary>See the games</summary>
      <ul
        className="mt-4 grid gap-3 sm:grid-cols-2"
        aria-label="Games in your sitting"
      >
        {GAMES.map(([label, icon]) => (
          <li
            key={label}
            className="flex min-h-12 items-center gap-3 rounded border border-[var(--line)] bg-[var(--panel)] p-3"
          >
            <Icon name={icon} />
            <span>{label}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}
