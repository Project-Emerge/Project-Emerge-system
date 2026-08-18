import { useTheme, type ThemePreference } from "../services/theme-context";

const options: Array<{ value: ThemePreference; label: string }> = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

export function ThemeSwitcher(): React.JSX.Element {
  const { preference, setPreference } = useTheme();

  return (
    <div className="theme-switcher" role="group" aria-label="Color theme">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={preference === option.value}
          onClick={() => setPreference(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
