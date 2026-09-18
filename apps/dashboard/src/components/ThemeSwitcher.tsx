import { useLocale } from "../services/locale-context";
import { useTheme, type ThemePreference } from "../services/theme-context";

export function ThemeSwitcher(): React.JSX.Element {
  const { preference, setPreference } = useTheme();
  const { t } = useLocale();

  const options: Array<{ value: ThemePreference; label: string }> = [
    { value: "system", label: t.theme.system },
    { value: "light", label: t.theme.light },
    { value: "dark", label: t.theme.dark },
  ];

  return (
    <div className="theme-switcher" role="group" aria-label={t.theme.ariaLabel}>
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
