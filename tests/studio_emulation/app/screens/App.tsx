import { appear } from "../animations";
import { NicheScreen } from "./NicheScreen";

export function App() {
  return (
    <div data-component="App">
      <style>{appear}</style>
      <NicheScreen onOpen={(id) => console.log(id)} />
    </div>
  );
}
