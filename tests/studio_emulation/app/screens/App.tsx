import { NicheScreen } from "./NicheScreen";

export function App() {
  return (
    <div data-component="App">
      <NicheScreen onOpen={(id) => console.log(id)} />
    </div>
  );
}
