interface PageContainerProps {
  children: React.ReactNode;
  maxWidth?: 'xl' | '3xl' | '4xl' | '6xl' | '7xl';
  className?: string;
  style?: React.CSSProperties;
}

const maxWidthClass = {
  xl: 'max-w-xl',
  '3xl': 'max-w-3xl',
  '4xl': 'max-w-4xl',
  '6xl': 'max-w-6xl',
  '7xl': 'max-w-7xl',
} as const;

export default function PageContainer({ children, maxWidth = '4xl', className = '', style }: PageContainerProps) {
  return (
    <div className={`${maxWidthClass[maxWidth]} mx-auto px-2 sm:px-4 ${className}`} style={style}>
      {children}
    </div>
  );
}
