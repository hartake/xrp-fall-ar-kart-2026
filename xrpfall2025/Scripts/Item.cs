using Godot;
using System;
using System.Collections.Generic;
using System.Diagnostics;

/// <summary>
/// What the player will collide with to change their state temporarily.
/// Items are NOT rigidbodies, players should be able to just pass through them
/// And with a collision check we see if the player gets the item or not
/// </summary>
public partial class Item : Node
{
	// Upon player collision, the item will disappear and a random
	// number generator will run to select which item the player will get. 
	// The numbers for the random number generator will coorespond with an enum
	// listing the various items the player can interact with. 
	// Upon selection, the item will be used immediately and disappear before 
	// reloading on the screen after a set period of time. 
	// The item will set off an event that will notify the player to change its state
	// accordingly.
	public event ItemCollisionDelegate OnItemCollision;
	//private Player player; //Item manager will pass in this information
	private List<Car> cars;

	//references necessary for collision bounds
	
	//[Export] private CsgMesh3D cylinder = null;
	[Export] private VisualInstance3D cylinder = null;
	[Export] private Area3D area3d = null;

	private Stopwatch timer = null;

	//for gizmos debugging
	private Color color;

	// Store which car hit this item for burst direction
	private Vector3 lastHitDirection = Vector3.Forward;

	private Vector3 Position
	{
		get { return area3d.Position; }
		set { area3d.Position = value; }
	}

	/// <summary>
	/// Returns the Axis Aligned Bounding Box centered on this Item
	/// </summary>
	public Aabb AABB
	{
		get
		{
			Aabb temp = cylinder.GetAabb();
			temp.Position = area3d.GlobalPosition - temp.Size / 2;
			return temp;
		}
	}

	// Called when the node enters the scene tree for the first time.
	public override void _Ready()
	{
		color = new Color("YELLOW");

		OnItemCollision += SelectItem;
		//OnItemCollision += StartTimer;
		OnItemCollision += HideModel;
		//node path will need to be updated when we get a formal player car model
		Position = new Vector3(2, 0, 1);
		timer = new Stopwatch();
	}

	// Called every frame. 'delta' is the elapsed time since the previous frame.
	public override void _Process(double delta)
	{
		//spin
		//cylinder.RotateObjectLocal(new Vector3(0, 1, 0), (float)Mathf.DegToRad(1));
		// Face closest car + swing + bob + sway
		float t = (float)(Time.GetTicksMsec() * 0.004);
		float swingY = (float)Mathf.Sin(t) * Mathf.DegToRad(45);
		float sway = (float)Mathf.Sin(t * 0.7) * Mathf.DegToRad(20);
		float bob = (float)Mathf.Sin(t * 1.5) * 0.3f;

		float faceY = 0;
		if (cars != null && cars.Count > 0)
		{
			Car closest = cars[0];
			float minDist = float.MaxValue;
			foreach (Car c in cars)
			{
				float d = area3d.GlobalPosition.DistanceTo(c.AABB.GetCenter());
				if (d < minDist) { minDist = d; closest = c; }
			}
			Vector3 dir = closest.AABB.GetCenter() - area3d.GlobalPosition;
			faceY = Mathf.Atan2(dir.X, dir.Z);
		}

		cylinder.Rotation = new Vector3(sway, faceY + swingY, cylinder.Rotation.Z);
		cylinder.Position = new Vector3(cylinder.Position.X, bob + 0.5f, cylinder.Position.Z);
		
		//if a timer is going, then reset the item and the timer
		if (timer.ElapsedMilliseconds > 10000)
		{
			ClearTimer();
			ShowModel();
		}
		//otherwise read for collisions with the player
		else if (timer.ElapsedMilliseconds >= 0)
		{
			foreach(Car c in cars)
			{
				//check for if this item has overlapped with the player.
				if (AABB.Intersects(/*player*/c.AABB))
				{
					//GD.Print("I'm Colliding!");
					//invoke event
					//if (OnItemCollision != null) OnItemCollision();

					// Get car's forward direction from its CharacterBody3D
					var body = c.GetNodeOrNull<CharacterBody3D>("Node3D/Player") ?? c.GetNode<CharacterBody3D>("Node3D/CharacterBody3D");
					if (body != null)
						lastHitDirection = -body.GlobalTransform.Basis.Z.Normalized();
					else
						lastHitDirection = (c.AABB.GetCenter() - area3d.GlobalPosition).Normalized();

					OnItemCollision?.Invoke(c); //shorthand for above
				
					StartTimer(c);

					//have the model be hidden from the scene 
					//unsubscribe from OnItemCollision Event 
				}
			}
			
		}

		//GD.Print("Item timer: " + timer.ElapsedMilliseconds);

		//DebugDraw3D.DrawBox(AABB.Position, Godot.Quaternion.Identity, Vector3.One, color);
		//DebugDraw3D.DrawAabb(AABB, color);
	}

	/// <summary>
	/// Random number generator to select what item the player drew upon collision
	/// Reason this is being done in the item rather than the item manager is because
	/// should this project expand into multiplayer, the event would only signal to the 
	/// specific player that collided with this item.
	/// </summary>
	private void SelectItem(Car c)
	{
		Random rng = new Random();
		//will need to manually update should we choose to update the list of items
		//currently no error checking of if the selected item is valid in the enum list
		int result = rng.Next(0, 3);
		//c.Current = (States)result;
		c.StoreItem((States)result);
		
	}

	/// <summary>
	/// Since parameterized constructors are inacessible in Godot, 
	/// this function will be called immediately after initializing 
	/// this instance in a different scene
	/// </summary>
	/// <param name="cars">Reference to all car instances in scene</param>
	/// <param name="position">Where do we spawn this item</param>
	public void CustomInit(List<Car> cars, Vector3 position)
	{
		//this.player = player;
		this.cars = cars;
		Position = position;
	}

	/// <summary>
	/// The time which the item stays hidden before respawning
	/// </summary>
	/// <param name="c">Unused param, however associated event 
	/// requires reference to a specific car which collided with the item</param>
	private void StartTimer(Car c)
	{
		timer.Restart();
		//unsubscribe from event
		//OnItemCollision -= StartTimer;
		OnItemCollision -= SelectItem;
		OnItemCollision -= HideModel;
	}

	/// <summary>
	/// Resets the timer
	/// </summary>
	private void ClearTimer()
	{
		timer.Reset();
		//resubscribe to event
		//OnItemCollision += StartTimer;
		OnItemCollision += SelectItem;
		OnItemCollision += HideModel;
	}

	/// <summary>
	/// Hides the item box and spawns burst particles
	/// </summary>
	private void HideModel(Car c)
	{
		SpawnBurst();
		cylinder.Hide();
	}

	/// <summary>
	/// Crisp pop with pieces flying forward along car direction,
	/// bouncing off floor, then fading out
	/// </summary>
	private void SpawnBurst()
	{
		var rng = new Random();
		Vector3 forward = lastHitDirection;
		forward.Y = 0;
		if (forward.LengthSquared() > 0.01f)
			forward = forward.Normalized();
		else
			forward = Vector3.Forward;

		Vector3 side = new Vector3(-forward.Z, 0, forward.X);

		for (int i = 0; i < 50; i++)
		{
			var piece = new CsgBox3D();
			piece.Size = new Vector3(0.4f, 0.4f, 0.4f);

			// Bright rainbow colors like MK item box shards
			var mat = new StandardMaterial3D();
			Color[] colors = {
				new Color(1, 0.2f, 0.2f),    // red
				new Color(0.2f, 0.5f, 1),     // blue
				new Color(1, 0.9f, 0.1f),     // yellow
				new Color(0.2f, 1, 0.4f),     // green
				new Color(1, 0.4f, 0.8f),     // pink
				new Color(0.6f, 0.3f, 1),     // purple
			};
			mat.AlbedoColor = colors[rng.Next(colors.Length)];
			piece.Material = mat;

			area3d.GetParent().AddChild(piece);
			piece.GlobalPosition = area3d.GlobalPosition + new Vector3(0, 0.5f, 0);

			// Spread: forward bias + some sideways + upward
			float fwd = (float)(rng.NextDouble() * 12 + 2);            // 2-8 forward
			float sideAmt = (float)(rng.NextDouble() * 2 - 1) * 4;    // ±4 sideways
			float peakY = (float)(rng.NextDouble() * 2.5 + 1);        // 1-3.5 up

			Vector3 scatter = forward * fwd + side * sideAmt;

			var peakPos = piece.GlobalPosition + new Vector3(scatter.X, peakY, scatter.Z);
			var floorPos = new Vector3(peakPos.X, 0.1f, peakPos.Z);
			var bouncePos = new Vector3(floorPos.X, peakY * 0.2f, floorPos.Z);
			var restPos = new Vector3(bouncePos.X, 0.1f, bouncePos.Z);

			// Random spin while flying
			var spinAxis = new Vector3(
				(float)(rng.NextDouble() * 2 - 1),
				(float)(rng.NextDouble() * 2 - 1),
				(float)(rng.NextDouble() * 2 - 1)
			).Normalized();
			float spinAmount = (float)(rng.NextDouble() * 10 + 5);

			var tween = piece.CreateTween();

			// Phase 1: pop upward and forward (fast, crisp)
			tween.SetParallel(true);
			tween.TweenProperty(piece, "global_position", peakPos, 0.3f)
				.SetEase(Tween.EaseType.Out).SetTrans(Tween.TransitionType.Back);
			tween.TweenProperty(piece, "rotation", spinAxis * spinAmount, 0.3f);
			tween.SetParallel(false);

			// Phase 2: fall to floor
			tween.TweenProperty(piece, "global_position", floorPos, 0.4f)
				.SetEase(Tween.EaseType.In).SetTrans(Tween.TransitionType.Quad);

			// Phase 3: small bounce
			tween.TweenProperty(piece, "global_position", bouncePos, 0.2f)
				.SetEase(Tween.EaseType.Out);

			// Phase 4: settle
			tween.TweenProperty(piece, "global_position", restPos, 0.2f)
				.SetEase(Tween.EaseType.In);

			// Phase 5: fade out (shrink)
			tween.TweenProperty(piece, "scale", Vector3.Zero, 0.3f)
				.SetEase(Tween.EaseType.In);
			tween.TweenCallback(Callable.From(() => piece.QueueFree()));
		}
	}

	/// <summary>
	/// Unhides the item box if it was hidden
	/// </summary>
	private void ShowModel()
	{
		cylinder.Show();
	}
}
