import { useLocale, type Locale } from "../services/locale-context";

const options: Array<{ value: Locale; label: string }> = [
  { value: "it", label: "IT" },
  { value: "en", label: "EN" },
];

export function LanguageSwitcher(): React.JSX.Element {
  const { locale, setLocale, t } = useLocale();

  return (
    <div className="language-switcher" role="group" aria-label={t.language.ariaLabel}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={locale === option.value}
          onClick={() => setLocale(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
