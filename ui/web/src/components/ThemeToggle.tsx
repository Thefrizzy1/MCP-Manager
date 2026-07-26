import { Moon, Sun } from 'lucide-react'
import { toggleTheme, useTheme } from '@/lib/theme'
import { Button } from './ui/Button'

export function ThemeToggle() {
  const theme = useTheme()
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      title="Toggle theme"
      aria-label="Toggle theme"
      onClick={toggleTheme}
    >
      {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
    </Button>
  )
}
