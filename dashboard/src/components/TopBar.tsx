interface TopBarProps {
  onResetCamera: () => void; // Added onResetCamera prop type
}

function TopBar({onResetCamera }: TopBarProps) {
  return (
    <div className="top-bar">
      <select onChange={_ => {/*TODO send command*/}}>
        <option value="circle">Circle</option>
        <option value="arrow">Arrow</option>
      </select>
      <button onClick={onResetCamera}>Reset Camera</button> 
    </div>
  );
}

export default TopBar;
