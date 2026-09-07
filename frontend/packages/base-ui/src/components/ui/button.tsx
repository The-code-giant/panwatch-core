import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../../cn'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full text-[13px] font-semibold transition-colors duration-150 active:translate-y-px disabled:pointer-events-none disabled:opacity-40',
  {
    variants: {
      variant: {
        // Bright Desk: the one lime pill. The user's action, nothing else.
        default:
          'bg-primary text-primary-foreground font-bold hover:bg-[hsl(var(--primary)/0.85)]',
        secondary: 'border border-border text-foreground hover:bg-muted',
        outline: 'border border-border bg-background text-foreground hover:bg-muted',
        destructive: 'bg-destructive/10 text-destructive hover:bg-destructive/15',
        ghost: 'text-muted-foreground hover:text-foreground hover:bg-accent',
        link: 'text-foreground underline underline-offset-4 hover:no-underline',
      },
      size: {
        default: 'h-10 px-4 py-2.5',
        sm: 'h-8 px-3 text-[12px]',
        lg: 'h-11 px-6',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = 'Button'

export { Button, buttonVariants }
