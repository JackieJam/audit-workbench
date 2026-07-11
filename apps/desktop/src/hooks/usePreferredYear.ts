import { useEffect } from "react";

/** 当 Agent 通过 ui_actions 指定年份时，同步到面板内年份选择器。 */
export function usePreferredYear(
  preferredYear: number | null | undefined,
  years: number[],
  setYear: (y: number) => void,
) {
  useEffect(() => {
    if (preferredYear != null && years.includes(preferredYear)) {
      setYear(preferredYear);
    }
  }, [preferredYear, years, setYear]);
}
