using Godot;
using System.Collections.Generic;

/// <summary>
/// Sets us the race scene
/// </summary>
public partial class SceneManager : Node
{
	//parts involved in the scene.
	[Export] private Node carManager;
	[Export] private Node track;
	[Export] private Node track_loader;
	[Export] private Node itemManager;
	[Export] private Node killPlane;
	[Export] private Node finishline;
	[Export] private Node checkpointManager;

	public override void _Ready()
	{
		//get the correct cooresponding child of each node
		CarManager carManagerCM = (CarManager)carManager;
		//Track trackT = (Track)track;
		ItemManager itemManagerIM = (ItemManager)itemManager;
		KillPlane killPlaneKP = (KillPlane)killPlane;
		Finishline finishlineFL = (Finishline)finishline;
		CheckpointManager checkpointManagerCM = (CheckpointManager)checkpointManager;
		
		
		//initialize everyone
		killPlaneKP.Init(carManagerCM.Cars);
		
		//trackT.Init();
		track_loader.Call("Init");
		var cp = track_loader.Get("checkpoints").As<Godot.Collections.Array<Vector3>>();
		//GD.Print(cp);
		var sp = track_loader.Get("startingPoint").As<Vector3>();
		var ip = track_loader.Get("innerPath").As<Path3D>();
		
		//checkpointManagerCM.SpawnCheckpoints(trackT.Checkpoints);
		checkpointManagerCM.SpawnCheckpoints(cp);
		
		//carManagerCM.Init(trackT.StartingPoint, trackT.Path3D, checkpointManagerCM.TotalCheckpoints);
		carManagerCM.Init(sp, ip, checkpointManagerCM.TotalCheckpoints);
		
		//checkpointManagerCM.Init(carManagerCM.Cars, trackT.Checkpoints);
		checkpointManagerCM.Init(carManagerCM.Cars, cp);
		
		//finishlineFL.Init(carManagerCM.Cars, trackT.StartingPoint); 
		finishlineFL.Init(carManagerCM.Cars, sp);
		
		//itemManagerIM.Init(carManagerCM.Cars, trackT.Path3D);
		itemManagerIM.Init(carManagerCM.Cars, ip);

		//subscribe Player and Killplane Events
		foreach (Car c in carManagerCM.Cars)
		{
			//GD.Print("Car: ", c, ", Type of c: ", c.GetType());
			//killPlaneKP.IsCollidingKillPlane += c.ReturnToTrack;
			killPlaneKP.IsCollidingKillPlane += c.ToPreviousCheckpoint;

			finishlineFL.OnCrossingEvent += c.IncrementLap;
			finishlineFL.OnCrossingEvent += c.ClearCheckpoints;
		}
		GetNode<Control>("CanvasLayer/HUD").Call("start_countdown");
	}

}
