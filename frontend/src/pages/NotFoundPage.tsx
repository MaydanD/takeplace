import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <main className="page">
      <h1>Страница не найдена</h1>
      <p>
        <Link to="/">На главную</Link>
      </p>
    </main>
  );
}
