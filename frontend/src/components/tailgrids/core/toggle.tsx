import { cn } from "@/utils/cn";
import { useId, type ComponentProps } from "react";

type PropsType = Omit<ComponentProps<"input">, "size"> & {
  label?: string;
  size?: "sm" | "md";
};

export function Toggle({
  size = "sm",
  className,
  label,
  ...inputProps
}: PropsType) {
  const id = useId();

  return (
    <label
      htmlFor={id}
      className={cn(
        "flex max-w-fit cursor-pointer items-center gap-2 select-none",
        inputProps.disabled && "cursor-not-allowed"
      )}
    >
      {/* label 안에는 문장 수준 콘텐츠만 둔다 (div 대신 span — 크기가 필요한 트랙은 block) */}
      <span className="relative">
        <input
          type="checkbox"
          id={id}
          className={cn("peer sr-only", className)}
          {...inputProps}
        />

        {/* 꺼짐 트랙·흰 손잡이·초점 링은 인접 색과 3:1 이상 (꺼짐 #767676 4.542:1, 켜짐·초점 #3758f9 5.348:1) */}
        <span
          className={cn(
            "block peer-checked:bg-toggle-active-background peer-focus:ring-2 peer-focus:ring-offset-2 peer-focus:ring-toggle-active-background h-5 w-9 rounded-full bg-toggle-default-background peer-disabled:bg-toggle-disabled-background",
            {
              "h-5 w-9": size === "sm",
              "h-6 w-11": size === "md"
            }
          )}
        />

        <span
          className={cn(
            "absolute top-1/2 left-0.5 flex -translate-y-1/2 items-center justify-center rounded-full bg-white peer-disabled:bg-toggle-disabled-foreground shadow-sm transition-all peer-checked:right-0.5 peer-checked:translate-x-full",
            {
              "size-4": size === "sm",
              "size-5": size === "md"
            }
          )}
        />
      </span>

      {label && (
        <span
          className={cn(
            "text-sm font-medium text-text-50",
            inputProps.disabled && "text-text-200"
          )}
        >
          {label}
        </span>
      )}
    </label>
  );
}
