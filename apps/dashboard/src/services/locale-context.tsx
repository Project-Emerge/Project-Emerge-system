import {
  createContext,
  useContext,
  useLayoutEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from "react";
import type { Locale, TranslationDictionary } from "../i18n/types";
import { it } from "../i18n/it";
import { en } from "../i18n/en";

export type { Locale, TranslationDictionary };

type LocaleContextValue = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: TranslationDictionary;
};

const dictionaries: Record<Locale, TranslationDictionary> = { it, en };

const LocaleContext = createContext<LocaleContextValue | null>(null);
export const LOCALE_STORAGE_KEY = "project-emerge-locale";

function isLocale(value: string | null): value is Locale {
  return value === "it" || value === "en";
}

function storedLocale(): Locale {
  try {
    const stored = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    return isLocale(stored) ? stored : "it";
  } catch {
    return "it";
  }
}

type LocaleProviderProps = PropsWithChildren<{
  initialLocale?: Locale;
}>;

export function LocaleProvider({ children, initialLocale }: LocaleProviderProps): React.JSX.Element {
  const [locale, setLocale] = useState<Locale>(() => initialLocale ?? storedLocale());

  useLayoutEffect(() => {
    document.documentElement.lang = locale;
    try {
      window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
    } catch {
      // Preference still works in-memory if storage is unavailable.
    }
  }, [locale]);

  const value = useMemo<LocaleContextValue>(
    () => ({
      locale,
      setLocale,
      t: dictionaries[locale],
    }),
    [locale],
  );

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale(): LocaleContextValue {
  const context = useContext(LocaleContext);
  if (!context) {
    // Default fallback to Italian when rendered outside LocaleProvider (e.g. simple unit tests)
    return {
      locale: "it",
      setLocale: () => {},
      t: it,
    };
  }
  return context;
}
