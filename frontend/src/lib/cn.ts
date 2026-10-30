import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** The only class-merging helper. Later Tailwind classes win. */
export const cn = (...inputs: ClassValue[]) => twMerge(clsx(inputs));
